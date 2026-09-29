from .base import ATSAdapter


class AshbyAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("ashby", ("ashbyhq.com",), aliases={"_systemfield_name": "Full name",
                         "_systemfield_email": "Email", "_systemfield_phone": "Phone"})
