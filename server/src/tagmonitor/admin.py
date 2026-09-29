"""Operator commands: python -m tagmonitor.admin make-admin you@example.com

There's deliberately no "become admin" API: admin rights are granted from a shell that
already has database access.
"""

import asyncio

import typer
from psycopg import AsyncConnection

from tagmonitor.config import get_settings

app = typer.Typer(add_completion=False)


@app.command()
def make_admin(email: str) -> None:
    """Give an existing account access to the ops page."""

    async def promote() -> int:
        async with await AsyncConnection.connect(get_settings().database_url) as conn:
            cursor = await conn.execute(
                "UPDATE users SET is_admin = true WHERE email = %s", (email,)
            )
            await conn.commit()
            return cursor.rowcount

    if asyncio.run(promote()) == 0:
        typer.echo(f"no account with email {email}")
        raise typer.Exit(1)
    typer.echo(f"{email} is now an admin")


@app.callback()
def main() -> None:
    """tag-monitor operator commands."""


if __name__ == "__main__":
    app()
