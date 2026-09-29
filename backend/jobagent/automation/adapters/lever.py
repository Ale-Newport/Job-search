from .base import ATSAdapter


class LeverAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("lever", ("lever.co",), aliases={"name": "Full name", "email": "Email",
                         "phone": "Phone", "org": "Current company", "urls[LinkedIn]": "LinkedIn"})
