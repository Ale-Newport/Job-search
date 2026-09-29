from .base import ATSAdapter


class TaleoAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("taleo", ("taleo.net",), capability="Labelled HTML controls; custom legacy controls and account forms require takeover.")
