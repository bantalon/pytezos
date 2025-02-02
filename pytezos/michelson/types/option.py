from typing import List, Optional, Type

from pytezos.michelson.types.base import MichelsonType
from pytezos.michelson.micheline import parse_micheline_value, Micheline
from pytezos.context.abstract import AbstractContext


class NoneLiteral(Micheline, prim='None'):
    pass


class SomeLiteral(Micheline, prim='Some', args_len=1):
    pass


class OptionType(MichelsonType, prim='option', args_len=1):

    def __init__(self, item: Optional[MichelsonType]):
        super(OptionType, self).__init__()
        self.item = item

    def __lt__(self, other: 'OptionType') -> bool:
        if other.item is None:
            return False
        elif self.item is None:
            return True
        else:
            return self.item < other.item

    def __eq__(self, other: 'OptionType') -> bool:
        return self.item == other.item

    def __hash__(self):
        return hash(self.item)

    def __repr__(self):
        return f'{repr(self.item)}?' if self.item else 'None'

    @staticmethod
    def none(some_type: Type[MichelsonType]) -> 'OptionType':
        cls = OptionType.create_type(args=[some_type])
        return cls(None)

    @staticmethod
    def from_some(item: MichelsonType) -> 'OptionType':
        cls = OptionType.create_type(args=[item.get_anon_type()])
        return cls(item)

    @classmethod
    def dummy(cls, context: AbstractContext) -> 'OptionType':
        return cls(None)

    @classmethod
    def generate_pydoc(cls, definitions: list, inferred_name=None, comparable=False):
        name = cls.field_name or cls.type_name or inferred_name
        arg_doc = cls.args[0].generate_pydoc(definitions, inferred_name=name)
        return f'{arg_doc} || None'

    @classmethod
    def from_micheline_value(cls, val_expr):
        item = parse_micheline_value(val_expr, {
            ('Some', 1): lambda x: cls.args[0].from_micheline_value(x[0]),
            ('None', 0): lambda x: None
        })
        return cls(item)

    @classmethod
    def from_python_object(cls, py_obj):
        if py_obj is None:
            item = None
        else:
            item = cls.args[0].from_python_object(py_obj)
        return cls(item)

    def is_none(self) -> bool:
        return self.item is None

    def get_some(self) -> MichelsonType:
        assert not self.is_none()
        return self.item

    def to_literal(self) -> Type[Micheline]:
        if self.is_none():
            return NoneLiteral
        else:
            return SomeLiteral.create_type(args=[self.item.to_literal()])

    def to_micheline_value(self, mode='readable', lazy_diff=False):
        if self.is_none():
            return {'prim': 'None'}
        else:
            arg = self.item.to_micheline_value(mode=mode, lazy_diff=lazy_diff)
            return {'prim': 'Some', 'args': [arg]}

    def to_python_object(self, try_unpack=False, lazy_diff=False, comparable=False):
        if self.is_none():
            return None
        else:
            return self.item.to_python_object(try_unpack=try_unpack,
                                              lazy_diff=lazy_diff,
                                              comparable=comparable)

    def merge_lazy_diff(self, lazy_diff: List[dict]) -> 'MichelsonType':
        item = None if self.is_none() else self.item.merge_lazy_diff(lazy_diff)
        return type(self)(item)

    def aggregate_lazy_diff(self, lazy_diff: List[dict], mode='readable') -> 'MichelsonType':
        item = None if self.is_none() else self.item.aggregate_lazy_diff(lazy_diff, mode=mode)
        return type(self)(item)

    def attach_context(self, context: AbstractContext, big_map_copy=False):
        if not self.is_none():
            self.item.attach_context(context, big_map_copy=big_map_copy)
