"""CLI entry point for a-stock-quant — A-share real-time monitoring.

Commands:
    astock start [codes...]   Start monitoring
    astock stop               Stop monitoring
    astock add <stock>        Add stock to watchlist
    astock remove <stock>     Remove stock from watchlist
    astock list               Show current watchlist with latest prices
    astock monitor            Live TUI monitoring view
    astock alerts [-n 20]     Show recent alerts
    astock history <stock>    Show price history
    astock export <stock>     Export snapshot data to CSV
"""

import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from a_stock_quant.config import load_config, reload_config
from a_stock_quant.data.ticker import resolve_ticker

app = typer.Typer(
    name="astock",
    help="A-Stock real-time quant monitor",
    no_args_is_help=True,
)

console = Console()
_engine = None


def _get_engine():
    """Lazy-init the monitor engine."""
    global _engine
    if _engine is None:
        from a_stock_quant.engine.monitor import MonitorEngine

        config = load_config()
        _engine = MonitorEngine(config)
    return _engine


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---- Commands ----


@app.command()
def start(
    codes: list[str] = typer.Argument(None, help="Stock codes or Chinese names to monitor"),
    interval: int = typer.Option(5, "--interval", "-i", help="Poll interval in seconds"),
    no_web: bool = typer.Option(False, "--no-web", help="Skip web dashboard"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
    config_path: str = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
):
    """Start real-time monitoring."""
    _setup_logging(verbose)

    config = load_config(Path(config_path) if config_path else None)
    if interval != 5:
        config["poll_interval_seconds"] = interval

    engine = _get_engine()
    engine.__init__(config)  # re-init with merged config

    # Resolve CLI codes
    if codes:
        resolved = []
        for c in codes:
            try:
                resolved.append(resolve_ticker(c))
            except ValueError as e:
                console.print(f"[red]Error:[/red] {e}")
                raise typer.Exit(1)
        config["watchlist"] = resolved

    async def _run():
        await engine.start()

        if no_web or not config.get("web", {}).get("enabled", True):
            # Just the monitoring loop
            console.print("[green]Monitoring started. Press Ctrl+C to stop.[/green]")
            try:
                while engine._running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            # Start web dashboard
            from a_stock_quant.web.app import create_app
            import uvicorn

            web_cfg = config["web"]
            app_fastapi = create_app(engine)

            class _Server(uvicorn.Server):
                def install_signal_handlers(self):
                    pass

            server = _Server(
                uvicorn.Config(
                    app_fastapi,
                    host=web_cfg["host"],
                    port=web_cfg["port"],
                    log_level="warning",
                )
            )

            console.print(
                f"[green]Monitoring started.[/green] "
                f"Dashboard at [bold cyan]http://{web_cfg['host']}:{web_cfg['port']}[/bold cyan]"
            )

            try:
                await asyncio.gather(
                    server.serve(),
                    engine._task if engine._task else asyncio.sleep(0),
                )
            except KeyboardInterrupt:
                pass
            finally:
                server.should_exit = True

        await engine.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")


@app.command()
def stop():
    """Stop the running monitor via PID file."""
    pid_file = Path("data/monitor.pid")
    if not pid_file.exists():
        console.print("[yellow]No running monitor found (PID file missing).[/yellow]")
        raise typer.Exit(0)

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Sent stop signal to PID {pid}.[/green]")
    except ProcessLookupError:
        console.print("[yellow]Process not found. Cleaning up PID file.[/yellow]")
    except PermissionError:
        console.print("[red]Permission denied. Try: kill {pid}[/red]".format(pid=pid))
        raise typer.Exit(1)

    pid_file.unlink(missing_ok=True)


@app.command()
def add(stock: str = typer.Argument(..., help="Stock code or Chinese name to add")):
    """Add a stock to the current watchlist (persisted to DB)."""
    engine = _get_engine()

    async def _add():
        from a_stock_quant.storage.database import Database

        config = load_config()
        db = Database(config["database"]["path"])
        await db.initialize()

        try:
            code = resolve_ticker(stock)
            watchlist = await db.load_watchlist()
            if code not in watchlist:
                watchlist.append(code)
                await db.save_watchlist(watchlist)
                console.print(f"[green]Added {code} to watchlist.[/green]")
            else:
                console.print(f"[yellow]{code} is already in the watchlist.[/yellow]")
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
        finally:
            await db.close()

    asyncio.run(_add())


@app.command()
def remove(stock: str = typer.Argument(..., help="Stock code to remove")):
    """Remove a stock from the watchlist."""
    engine = _get_engine()

    async def _remove():
        from a_stock_quant.storage.database import Database

        config = load_config()
        db = Database(config["database"]["path"])
        await db.initialize()

        try:
            code = resolve_ticker(stock)
            watchlist = await db.load_watchlist()
            if code in watchlist:
                watchlist.remove(code)
                await db.save_watchlist(watchlist)
                console.print(f"[green]Removed {code} from watchlist.[/green]")
            else:
                console.print(f"[yellow]{code} not in watchlist.[/yellow]")
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
        finally:
            await db.close()

    asyncio.run(_remove())


@app.command()
def list():
    """Show current watchlist with latest prices."""
    async def _list():
        from a_stock_quant.data.fetcher import QuoteFetcher
        from a_stock_quant.storage.database import Database

        config = load_config()
        db = Database(config["database"]["path"])
        await db.initialize()

        watchlist = await db.load_watchlist()
        if not watchlist:
            # Try config
            watchlist = config.get("watchlist", [])

        if not watchlist:
            console.print("[yellow]Watchlist is empty.[/yellow]")
            await db.close()
            return

        # Fetch live prices
        fetcher = QuoteFetcher()
        try:
            snapshots = await fetcher.fetch_batch(watchlist)
        except Exception as e:
            console.print(f"[red]Failed to fetch quotes: {e}[/red]")
            await db.close()
            return

        table = Table(title="Current Watchlist")
        table.add_column("Code", style="cyan")
        table.add_column("Name")
        table.add_column("Price", justify="right")
        table.add_column("Chg%", justify="right")
        table.add_column("PE(TTM)", justify="right")
        table.add_column("Turnover%", justify="right")

        for code in watchlist:
            snap = snapshots.get(code)
            if snap:
                chg_str = f"[{'red' if snap.change_pct >= 0 else 'green'}]{snap.change_pct:+.2f}%[/]"
                table.add_row(
                    code, snap.name,
                    f"{snap.price:.2f}",
                    chg_str,
                    f"{snap.pe_ttm:.1f}" if snap.pe_ttm > 0 else "-",
                    f"{snap.turnover_pct:.2f}%",
                )
            else:
                table.add_row(code, "N/A", "-", "-", "-", "-")

        console.print(table)
        await db.close()

    asyncio.run(_list())


@app.command()
def monitor(
    interval: int = typer.Option(1, "--interval", "-i", help="Refresh interval in seconds"),
):
    """Live TUI monitoring view (auto-refreshing table)."""
    from a_stock_quant.data.fetcher import QuoteFetcher
    from a_stock_quant.storage.database import Database

    async def _monitor():
        config = load_config()
        db = Database(config["database"]["path"])
        await db.initialize()
        watchlist = await db.load_watchlist() or config.get("watchlist", [])

        if not watchlist:
            console.print("[yellow]Watchlist is empty. Add stocks first.[/yellow]")
            await db.close()
            return

        fetcher = QuoteFetcher()

        def build_table(snapshots: dict) -> Table:
            t = Table(title="Real-Time Stock Prices")
            t.add_column("Code", style="cyan")
            t.add_column("Name")
            t.add_column("Price", justify="right")
            t.add_column("Chg%", justify="right")
            t.add_column("High", justify="right")
            t.add_column("Low", justify="right")
            t.add_column("PE", justify="right")
            t.add_column("Turnover%", justify="right")

            for code in watchlist:
                snap = snapshots.get(code)
                if snap:
                    chg = snap.change_pct
                    chg_str = f"[{'red' if chg >= 0 else 'green'}]{chg:+.2f}%[/]"
                    t.add_row(
                        code, snap.name,
                        f"{snap.price:.2f}", chg_str,
                        f"{snap.high:.2f}", f"{snap.low:.2f}",
                        f"{snap.pe_ttm:.1f}" if snap.pe_ttm > 0 else "-",
                        f"{snap.turnover_pct:.2f}%",
                    )
                else:
                    t.add_row(code, "-", "-", "-", "-", "-", "-", "-")
            return t

        console.print("[green]Live monitor — Ctrl+C to exit[/green]")
        with Live(console=console, refresh_per_second=2) as live:
            while True:
                try:
                    snapshots = await fetcher.fetch_batch(watchlist)
                    live.update(build_table(snapshots))
                except Exception as e:
                    console.print(f"[red]Fetch error: {e}[/red]")
                await asyncio.sleep(interval)

    try:
        asyncio.run(_monitor())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


@app.command()
def alerts(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of alerts to show"),
    stock: str = typer.Option(None, "--stock", "-s", help="Filter by stock code"),
):
    """Show recent alerts."""
    async def _alerts():
        from a_stock_quant.storage.database import Database

        config = load_config()
        db = Database(config["database"]["path"])
        await db.initialize()

        if stock:
            try:
                code = resolve_ticker(stock)
            except ValueError as e:
                console.print(f"[red]{e}[/red]")
                await db.close()
                return
            rows = await db.get_alerts_for_code(code, limit=limit)
        else:
            rows = await db.get_recent_alerts(limit=limit)

        if not rows:
            console.print("[dim]No alerts recorded yet.[/dim]")
        else:
            for a in rows:
                t = a["triggered_at"]
                sev_color = {"info": "blue", "warning": "yellow", "critical": "red"}.get(
                    a["severity"], "white"
                )
                console.print(
                    f"[dim]{t[:19]}[/dim] "
                    f"[{sev_color}]● {a['severity'].upper()}[/{sev_color}] "
                    f"[cyan]{a['name']}({a['code']})[/cyan] "
                    f"{a['message']}"
                )
        await db.close()

    asyncio.run(_alerts())


@app.command()
def history(
    stock: str = typer.Argument(..., help="Stock code or name"),
    minutes: int = typer.Option(60, "--minutes", "-m", help="Time window in minutes"),
):
    """Show price history for a stock."""
    from a_stock_quant.storage.database import Database

    async def _history():
        config = load_config()
        db = Database(config["database"]["path"])
        await db.initialize()

        try:
            code = resolve_ticker(stock)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            await db.close()
            return

        start = datetime.utcnow()
        from datetime import timedelta
        start = start - timedelta(minutes=minutes)
        snapshots = await db.get_snapshot_history(code, start=start)

        if not snapshots:
            console.print(f"[dim]No data for {code} in the last {minutes} minutes.[/dim]")
        else:
            table = Table(title=f"Price History: {snapshots[0].name}({code})")
            table.add_column("Time")
            table.add_column("Price", justify="right")
            table.add_column("Chg%", justify="right")
            table.add_column("Volume(est)", justify="right")

            for s in snapshots:
                t = s.fetched_at.strftime("%H:%M:%S")
                chg_str = f"[{'red' if s.change_pct >= 0 else 'green'}]{s.change_pct:+.2f}%[/]"
                table.add_row(t, f"{s.price:.2f}", chg_str, "-")
            console.print(table)

        await db.close()

    asyncio.run(_history())


@app.command()
def export(
    stock: str = typer.Argument(..., help="Stock code or name"),
    output: str = typer.Option(None, "--output", "-o", help="Output CSV file path"),
    days: int = typer.Option(7, "--days", "-d", help="Days of data to export"),
):
    """Export snapshot data to CSV."""
    from a_stock_quant.storage.database import Database

    async def _export():
        config = load_config()
        db = Database(config["database"]["path"])
        await db.initialize()

        try:
            code = resolve_ticker(stock)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            await db.close()
            return

        from datetime import timedelta
        start = datetime.utcnow() - timedelta(days=days)
        snapshots = await db.get_snapshot_history(code, start=start)

        if not snapshots:
            console.print(f"[dim]No data for {code} in the last {days} days.[/dim]")
            await db.close()
            return

        out_path = output or f"{code}_snapshots_{datetime.now().strftime('%Y%m%d')}.csv"
        import csv
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=[
                "code", "name", "price", "last_close", "open", "change_pct",
                "high", "low", "turnover_pct", "pe_ttm", "pe_static", "pb",
                "mcap_yi", "float_mcap_yi", "limit_up", "limit_down", "fetched_at",
            ])
            writer.writeheader()
            for s in snapshots:
                writer.writerow(s.to_dict())

        console.print(f"[green]Exported {len(snapshots)} snapshots to {out_path}[/green]")
        await db.close()

    asyncio.run(_export())


def main():
    app()


if __name__ == "__main__":
    main()
