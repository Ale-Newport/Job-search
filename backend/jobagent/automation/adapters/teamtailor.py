from .base import ATSAdapter


class TeamtailorAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("teamtailor", ("teamtailor.com",), aliases={"candidate[first_name]": "First name",
                         "candidate[last_name]": "Last name", "candidate[email]": "Email"})
