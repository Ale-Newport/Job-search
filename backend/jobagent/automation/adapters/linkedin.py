from .base import ATSAdapter


class LinkedInAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("linkedin", ("linkedin.com",), manual_only=True,
                         capability="Discovery links and prepared application data; submission uses manual handoff.")
