"""Tests for app.gunicorn_worker — custom Gunicorn worker with terminal WS."""

from unittest.mock import MagicMock

from app.gunicorn_worker import TerminalGeventWorker
from app.terminal_ws_handler import TerminalWSHandler


class TestTerminalGeventWorker:
    def test_inherits_from_pywsgi_worker(self):
        from gunicorn.workers.ggevent import GeventPyWSGIWorker

        assert issubclass(TerminalGeventWorker, GeventPyWSGIWorker)

    def test_uses_terminal_ws_handler(self):
        assert TerminalGeventWorker.wsgi_handler is TerminalWSHandler

    def test_handler_is_wsgi_handler_subclass(self):
        from gevent.pywsgi import WSGIHandler

        assert issubclass(TerminalWSHandler, WSGIHandler)
