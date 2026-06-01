"""Tests for app.gunicorn_worker — custom Gunicorn worker with remote WS."""

from unittest.mock import MagicMock

from app.gunicorn_worker import TerminalGeventWorker
from app.remote_ws_handler import RemoteWSHandler


class TestTerminalGeventWorker:
    def test_inherits_from_pywsgi_worker(self):
        from gunicorn.workers.ggevent import GeventPyWSGIWorker

        assert issubclass(TerminalGeventWorker, GeventPyWSGIWorker)

    def test_uses_remote_ws_handler(self):
        assert TerminalGeventWorker.wsgi_handler is RemoteWSHandler

    def test_handler_is_wsgi_handler_subclass(self):
        from gevent.pywsgi import WSGIHandler

        assert issubclass(RemoteWSHandler, WSGIHandler)
