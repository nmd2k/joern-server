def json_error(msg: str, *, code: str = "bad_request") -> dict[str, str]:
    return {"error": msg, "code": code}
