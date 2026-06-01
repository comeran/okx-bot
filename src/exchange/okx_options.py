from src.exchange.base import OKXBaseAdapter


class OKXOptionsAdapter(OKXBaseAdapter):
    def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(api_key, secret, passphrase, "option")
