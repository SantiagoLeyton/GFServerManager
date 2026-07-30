from app.logging_config import configure_logging
from app.wizard import run


if __name__ == "__main__":
    configure_logging()
    run()

