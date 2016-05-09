from commons import *
from accpatterns import patterns
from ssdbox import hostevent


class SuiteBase(object):
    def __init__(self, zone_size, chunk_size, traffic_size, **kwargs):
        self.zone_size = zone_size
        self.chunk_size = chunk_size
        self.traffic_size = traffic_size

        kwargs.setdefault("snake_size", None)
        kwargs.setdefault("stride_size", None)

        self.snake_size = kwargs['snake_size']
        self.stride_size = kwargs['stride_size']


class SRandomRead(SuiteBase):
    "Sequential write and then reandomly read it"
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        # write half
        self.write_iter = patterns.Sequential(op=OP_WRITE, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.zone_size,
                traffic_size=self.zone_size)

        self.read_iter = patterns.Random(op=OP_READ, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='interest_workload_start')

        for req in self.read_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='gc_start_timestamp')

        yield hostevent.ControlEvent(operation=OP_CLEAN)


class SRandomWrite(SuiteBase):
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        # write half
        self.write_iter = patterns.Random(op=OP_WRITE, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='gc_start_timestamp')

        yield hostevent.ControlEvent(operation=OP_CLEAN)

class SSequentialRead(SuiteBase):
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        self.write_iter = patterns.Sequential(op=OP_WRITE, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.zone_size,
                traffic_size=self.zone_size)

        self.read_iter = patterns.Sequential(op=OP_READ, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='interest_workload_start')

        for req in self.read_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='gc_start_timestamp')

        yield hostevent.ControlEvent(operation=OP_CLEAN)


class SSequentialWrite(SuiteBase):
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        # write half
        self.write_iter = patterns.Sequential(op=OP_WRITE, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='gc_start_timestamp')

        yield hostevent.ControlEvent(operation=OP_CLEAN)



class SSnake(SuiteBase):
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        self.write_iter = patterns.Snake(zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size, snake_size=self.snake_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req


class SFadingSnake(SuiteBase):
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        # write half
        self.write_iter = patterns.FadingSnake(zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size, snake_size=self.snake_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='gc_start_timestamp')

        yield hostevent.ControlEvent(operation=OP_CLEAN)


class SStrided(SuiteBase):
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        # write half
        self.write_iter = patterns.Strided(op=OP_WRITE, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size, stride_size=self.stride_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='gc_start_timestamp')

        yield hostevent.ControlEvent(operation=OP_CLEAN)


class SHotNCold(SuiteBase):
    def _prepare_iter(self):
        chunk_size = self.chunk_size

        # write half
        self.write_iter = patterns.HotNCold(op=OP_WRITE, zone_offset=0,
                zone_size=self.zone_size, chunk_size=self.chunk_size,
                traffic_size=self.traffic_size)

    def __iter__(self):
        self._prepare_iter()

        for req in self.write_iter:
            yield req

        yield hostevent.ControlEvent(operation=OP_BARRIER)
        yield hostevent.ControlEvent(operation=OP_REC_TIMESTAMP,
                arg1='gc_start_timestamp')

        yield hostevent.ControlEvent(operation=OP_CLEAN)

