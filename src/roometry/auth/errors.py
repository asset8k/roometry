class AuthFailure(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message

    def response(self):
        return {"detail": [{"code": self.code, "message": self.message}]}
