def __getattr__(name: str):
    if name == "AppSettings":
        from .config import AppSettings

        return AppSettings
    if name == "DailyNewsCollector":
        from .collector import DailyNewsCollector

        return DailyNewsCollector
    if name == "main":
        from .cli import main

        return main
    raise AttributeError(name)
