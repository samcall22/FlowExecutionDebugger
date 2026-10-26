# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

import truststore

# Trust the Windows certificate store so TLS-inspecting proxies (Zscaler, etc.) are accepted.
truststore.inject_into_ssl()

from flowdebugger.ui import main  # noqa: E402

if __name__ == "__main__":
    main()
