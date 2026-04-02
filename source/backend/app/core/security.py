from dataclasses import dataclass


@dataclass
class CurrentUser:
    id: int
    username: str
