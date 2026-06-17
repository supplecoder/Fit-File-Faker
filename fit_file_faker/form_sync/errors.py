"""Exception types that classify failures by how the pipeline should react.

The pipeline marks an email as read by default on ANY failure — retrying a
broken email just wastes GitHub Actions minutes and spams failure
notifications. The one exception is a `TransientError`: a failure that could
plausibly succeed if tried again later (a network blip, a 5xx, a Garmin
outage). Those leave the email unread so the next run retries it.

    FormSyncError
    ├── TransientError      → leave email UNREAD, retry next run
    └── ExpiredLinkError    → mark read (unrecoverable); re-export from FORM

Any other (non-FormSyncError) exception is treated as permanent: mark read,
fail the run once so a single notification is sent, and do not retry.
"""


class FormSyncError(RuntimeError):
    """Base class for form_sync pipeline errors."""


class TransientError(FormSyncError):
    """A failure that may succeed on retry (network error, 5xx, Garmin outage).

    The email is left unread so the next scheduled/dispatched run retries it.
    """


class ExpiredLinkError(FormSyncError):
    """The FORM presigned link's temporary credentials have expired.

    Unrecoverable — retrying can never succeed because the link is dead. The
    email is marked read (to stop it looping) and the user must re-export from
    the FORM app.
    """
