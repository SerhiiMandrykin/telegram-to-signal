import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def convert_ogg_to_m4a(input_path: str) -> str | None:
    """Convert OGG voice file to M4A for Signal iOS compatibility."""
    output_path = input_path.rsplit('.', 1)[0] + '.m4a'
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-c:a', 'aac', '-b:a', '64k',
            output_path
        ], check=True, capture_output=True)
        logger.info('Converted %s to %s', input_path, output_path)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error('Failed to convert OGG to M4A: %s', e.stderr.decode())
        return None
    except FileNotFoundError:
        logger.error('ffmpeg not found, cannot convert voice message')
        return None


def convert_m4a_to_ogg_opus(input_path: str) -> str | None:
    """Convert M4A voice file to OGG Opus for Telegram voice note compatibility."""
    basename = os.path.basename(input_path)
    name_without_ext = basename.rsplit('.', 1)[0] if '.' in basename else basename
    output_path = f'/media/{name_without_ext}.ogg'
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-c:a', 'libopus', '-b:a', '64k',
            output_path
        ], check=True, capture_output=True)
        logger.info('Converted %s to %s', input_path, output_path)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error('Failed to convert M4A to OGG Opus: %s', e.stderr.decode())
        return None
    except FileNotFoundError:
        logger.error('ffmpeg not found, cannot convert voice message')
        return None


def cleanup_files(*paths: str) -> None:
    """Clean up temporary media files."""
    for path in paths:
        if path and os.path.exists(path):
            os.remove(path)
            logger.info('File %s was deleted', path)
