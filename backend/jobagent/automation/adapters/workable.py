from .base import ATSAdapter


class WorkableAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("workable", ("workable.com",), aliases={"firstname": "First name", "lastname": "Last name"})
