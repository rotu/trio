import abc
import contextlib

import attr
from async_generator import async_generator, yield_

from . import _core
from .abc import HalfCloseableStream
from ._util import acontextmanager

__all__ = [
    "aclose_forcefully",
    "BrokenStreamError",
    "ClosedStreamError",
    "ClosedListenerError",
    "StapledStream",
]


async def aclose_forcefully(resource):
    """Close an async resource or async generator immediately, without
    blocking to do any graceful cleanup.

    :class:`~trio.abc.AsyncResource` objects guarantee that if their
    :meth:`~trio.abc.AsyncResource.aclose` method is cancelled, then they will
    still close the resource (albeit in a potentially ungraceful
    fashion). :func:`aclose_forcefully` is a convenience function that
    exploits this behavior to let you force a resource to be closed without
    blocking: it works by calling ``await resource.aclose()`` and then
    cancelling it immediately.

    Most users won't need this, but it may be useful on cleanup paths where
    you can't afford to block, or if you want to close an resource and don't
    care about handling it gracefully. For example, if
    :class:`~trio.ssl.SSLStream` encounters an error and cannot perform its
    own graceful close, then there's no point in waiting to gracefully shut
    down the underlying transport either, so it calls ``await
    aclose_forcefully(self.transport_stream)``.

    Note that this function is async, and that it acts as a checkpoint, but
    unlike most async functions it cannot block indefinitely (at least,
    assuming the underlying resource object is correctly implemented).

    """
    with _core.open_cancel_scope() as cs:
        cs.cancel()
        await resource.aclose()


class BrokenStreamError(Exception):
    """Raised when an attempt to use a stream a stream fails due to external
    circumstances.

    For example, you might get this if you try to send data on a stream where
    the remote side has already closed the connection.

    You *don't* get this error if *you* closed the stream – in that case you
    get :class:`ClosedStreamError`.

    This exception's ``__cause__`` attribute will often contain more
    information about the underlying error.

    """
    pass


class ClosedStreamError(Exception):
    """Raised when an attempt to use a stream fails because the stream was
    already closed locally.

    You *only* get this error if *your* code closed the stream object you're
    attempting to use by calling
    :meth:`~trio.abc.AsyncResource.aclose` or
    similar. (:meth:`~trio.abc.SendStream.send_all` might also raise this if
    you already called :meth:`~trio.abc.HalfCloseableStream.send_eof`.)
    Therefore this exception generally indicates a bug in your code.

    If a problem arises elsewhere, for example due to a network failure or a
    misbehaving peer, then you get :class:`BrokenStreamError` instead.

    """
    pass


class ClosedListenerError(Exception):
    """Raised when an attempt to use a listener fails because it was already
    closed locally.

    You *only* get this error if *your* code closed the stream object you're
    attempting to use by calling :meth:`~trio.abc.AsyncResource.aclose` or
    similar. Therefore this exception generally indicates a bug in your code.

    """
    pass


@attr.s(slots=True, cmp=False, hash=False)
class StapledStream(HalfCloseableStream):
    """This class `staples <https://en.wikipedia.org/wiki/Staple_(fastener)>`__
    together two unidirectional streams to make single bidirectional stream.

    Args:
      send_stream (~trio.abc.SendStream): The stream to use for sending.
      receive_stream (~trio.abc.ReceiveStream): The stream to use for
          receiving.

    Example:

       A silly way to make a stream that echoes back whatever you write to
       it::

          left, right = trio.testing.memory_stream_pair()
          echo_stream = StapledStream(SocketStream(left), SocketStream(right))
          await echo_stream.send_all(b"x")
          assert await echo_stream.receive_some(1) == b"x"

    :class:`StapledStream` objects implement the methods in the
    :class:`~trio.abc.HalfCloseableStream` interface. They also have two
    additional public attributes:

    .. attribute:: send_stream

       The underlying :class:`~trio.abc.SendStream`. :meth:`send_all` and
       :meth:`wait_send_all_might_not_block` are delegated to this object.

    .. attribute:: receive_stream

       The underlying :class:`~trio.abc.ReceiveStream`. :meth:`receive_some`
       is delegated to this object.

    """
    send_stream = attr.ib()
    receive_stream = attr.ib()

    async def send_all(self, data):
        """Calls ``self.send_stream.send_all``.

        """
        return await self.send_stream.send_all(data)

    async def wait_send_all_might_not_block(self):
        """Calls ``self.send_stream.wait_send_all_might_not_block``.

        """
        return await self.send_stream.wait_send_all_might_not_block()

    async def send_eof(self):
        """Shuts down the send side of the stream.

        If ``self.send_stream.send_eof`` exists, then calls it. Otherwise,
        calls ``self.send_stream.aclose()``.

        """
        if hasattr(self.send_stream, "send_eof"):
            return await self.send_stream.send_eof()
        else:
            return await self.send_stream.aclose()

    async def receive_some(self, max_bytes):
        """Calls ``self.receive_stream.receive_some``.

        """
        return await self.receive_stream.receive_some(max_bytes)

    async def aclose(self):
        """Calls ``aclose`` on both underlying streams.

        """
        try:
            await self.send_stream.aclose()
        finally:
            await self.receive_stream.aclose()