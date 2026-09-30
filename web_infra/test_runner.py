"""
Copyright 2024  Francais pour une Meilleure Mobilité

Author(s): Jeff Abrahamson <jeff@p27.eu>.

This file is part of the mobilito web application.

Mobilito is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

Mobilito is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public
License along with mobilito.  If not, see
<http://www.gnu.org/licenses/>.
"""

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class EnglishTestRunner(DiscoverRunner):
    """Run tests in English, whatever the site's default language.

    Tests assert on English copy. The site defaults to French, so
    once the French catalogue is compiled (compilemessages), pages
    render in French unless a test asks otherwise. Tests about
    language choice set it themselves.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._english = override_settings(LANGUAGE_CODE="en")
        self._english.enable()

    def teardown_test_environment(self, **kwargs):
        self._english.disable()
        super().teardown_test_environment(**kwargs)
