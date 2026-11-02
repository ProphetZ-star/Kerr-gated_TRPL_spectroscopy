"""Typed command failures survive IPC without confusing validation and device state."""
class CommandError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def error_status(exc, command=''):
    if isinstance(exc, CommandError): return exc.status_code
    if isinstance(exc, (FileNotFoundError, KeyError)): return 404
    if isinstance(exc, (FileExistsError,)): return 409
    if isinstance(exc, (ValueError, TypeError, OverflowError)) and command in {
        'plan', 'export', 'save', 'open', 'analysis_open', 'calibration', 'calibration_capture', 'calibration_extract', 'calibration_fit'}:
        return 422
    return 409
