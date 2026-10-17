"""Elscione release source - epub/pdf files from server.elscione.com."""

# Importing browser_session_settings registers the Browser Session Reuse tab: the
# config loader imports shelfmark.release_sources (and therefore this package) in
# every process that reads settings, the bypass helper subprocess included, so no
# shared settings file has to change.
from shelfmark.config import browser_session_settings as browser_session_settings

# Import to trigger registration
from shelfmark.release_sources.elscione import handler as handler
from shelfmark.release_sources.elscione import settings as settings
from shelfmark.release_sources.elscione import source as source
