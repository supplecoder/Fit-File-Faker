"""Apply fit-file-faker to rewrite FORM FIT files as a Garmin device.

Wraps the existing FitEditor and Profile machinery so the pipeline
can call a single function without caring about fit-file-faker internals.
The Profile is built from environment-sourced values rather than the
on-disk config file, keeping this module stateless and cloud-friendly.
"""

import logging
from pathlib import Path

from fit_file_faker.config import AppType, Profile
from fit_file_faker.fit_editor import FitEditor

_logger = logging.getLogger(__name__)

# Garmin manufacturer ID (fixed — we always target a Garmin device)
GARMIN_MANUFACTURER = 1


def build_profile(
    garmin_email: str,
    garmin_password: str,
    device_id: int,
    serial_number: int,
    software_version: int,
) -> Profile:
    """Build a fit-file-faker Profile from pipeline configuration values.

    Args:
        garmin_email: Garmin Connect account email.
        garmin_password: Garmin Connect account password.
        device_id: Garmin product ID to simulate (e.g. 4315 = Forerunner 965).
        serial_number: Device Unit ID. Should match the actual Garmin device
            for Training Effect and activity attribution to work correctly.
        software_version: Firmware version in FIT integer format
            (e.g. 2709 = v27.09 for Forerunner 965).

    Returns:
        A Profile configured for FORM sync with the specified Garmin device.
    """
    return Profile(
        name="form_sync",
        app_type=AppType.CUSTOM,
        garmin_username=garmin_email,
        garmin_password=garmin_password,
        fitfiles_path=Path("/tmp"),
        manufacturer=GARMIN_MANUFACTURER,
        device=device_id,
        serial_number=serial_number,
        software_version=software_version,
    )


def process_fit_file(fit_path: Path, profile: Profile) -> Path:
    """Rewrite a FORM FIT file to appear as the configured Garmin device.

    Modifies the manufacturer, product, serial number, and firmware fields
    in the FIT file's FileIdMessage and DeviceInfoMessage records while
    preserving all activity data (laps, records, heart rate, etc.).

    Output is written as {original_stem}_modified.fit in the same directory.

    Args:
        fit_path: Path to the original FORM .fit file.
        profile: Profile containing the target Garmin device configuration.

    Returns:
        Path to the modified .fit file.

    Raises:
        RuntimeError: If fit-file-faker fails to process the file.
    """
    output_path = fit_path.parent / f"{fit_path.stem}_modified.fit"
    _logger.info(f"Processing {fit_path.name} → {output_path.name}")

    editor = FitEditor(profile=profile)
    result = editor.edit_fit(fit_path, output=output_path)

    if result is None:
        raise RuntimeError(
            f"fit-file-faker failed to process {fit_path.name}. "
            "Run with verbose logging for details."
        )

    _logger.info(f"Device rewrite complete: {result.name}")
    return result
