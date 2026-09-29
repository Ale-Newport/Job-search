from .base import ATSAdapter


class GreenhouseAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("greenhouse", ("greenhouse.io", "greenhouse.com"), aliases={
            "job_application[first_name]": "First name", "job_application[last_name]": "Last name",
            "job_application[email]": "Email", "job_application[phone]": "Phone",
            "job_application[location]": "Location"})
