from .base import ATSAdapter


class IndeedAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("indeed", ("indeed.com", "indeed.co.uk"), manual_only=True,
                         capability="Discovery links and prepared application data; submission uses manual handoff.")
