"""OpenMDict shared build infrastructure.

Single source of truth for CSS theming, HTML helpers, and build utilities
used by all dictionary build scripts.

Usage from any build script:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from shared.theme import get_css, PRONUNCIATION, EXAMPLES
    from shared.html import esc, wrap_entry
    from shared.build import pack_mdx, generate_preview
"""

from shared.html import esc, wrap_entry
from shared.theme import get_css
from shared.build import (pack_mdx, generate_preview, write_mdict_source,
                         write_catalog_metadata)
