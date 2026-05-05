from server.domain.auth import AuthToken


class AuthService:
    def __init__(self, auth_token: AuthToken) -> None:
        self._auth_token = auth_token

    def login(self, token: str) -> None:
        self._auth_token.verify(token)
