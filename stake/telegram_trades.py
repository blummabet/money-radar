"""money-radar (18.10.2026): Ersatz für telegram_trades.py des alten Projekts.

stake_burst_push.py wird hier nur zum ERKENNEN der Bursts genutzt — senden tut der Geldradar selbst
(push.py, Test-Channel). Diese Funktion meldet „gesendet", damit die Erkennung die Bursts wie bisher
als push=True ins Buch schreibt (das Buch ist das, was build.py liest). Es geht NICHTS an Telegram.
"""


def send_trades_message(text: str, disable_preview: bool = True) -> bool:  # noqa: ARG001
    return True
