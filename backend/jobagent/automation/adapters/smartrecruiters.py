from .base import ATSAdapter


class SmartRecruitersAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("smartrecruiters", ("smartrecruiters.com",), aliases={"firstName": "First name",
                         "lastName": "Last name", "email": "Email"})
