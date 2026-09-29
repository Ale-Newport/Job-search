from .base import ATSAdapter


class ICIMSAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("icims", ("icims.com",), capability="Labelled HTML form controls including accessible frames; proprietary widgets require takeover.")
