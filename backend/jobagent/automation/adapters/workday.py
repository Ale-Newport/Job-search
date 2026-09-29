from .base import ATSAdapter


class WorkdayAdapter(ATSAdapter):
    def __init__(self):
        super().__init__("workday", ("myworkdayjobs.com", "myworkdaysite.com"),
                         capability="Labelled multi-step forms, frames and uploads. Account creation, complex date pickers and custom widgets require takeover.")
