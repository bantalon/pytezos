import sha3
from hashlib import sha256, sha512
from typing import List, Tuple, Callable, cast
from py_ecc import optimized_bls12_381 as bls12_381
from py_ecc.fields import optimized_bls12_381_FQ12 as FQ12

from pytezos.crypto.key import blake2b_32, Key
from pytezos.michelson.stack import MichelsonStack
from pytezos.michelson.types import BytesType, KeyType, SignatureType, BoolType, KeyHashType, SaplingStateType, \
    ListType, BLS12_381_G1Type, BLS12_381_G2Type, PairType
from pytezos.michelson.instructions.base import MichelsonInstruction, format_stdout
from pytezos.context.abstract import AbstractContext


def execute_hash(prim: str, stack: MichelsonStack, stdout: List[str], hash_digest: Callable[[bytes], bytes]):
    a = cast(BytesType, stack.pop1())
    a.assert_type_equal(BytesType)
    res = BytesType.from_value(hash_digest(bytes(a)))
    stack.push(res)
    stdout.append(format_stdout(prim, [a], [res]))


class Blake2bInstruction(MichelsonInstruction, prim='BLAKE2B'):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        execute_hash(cls.prim, stack, stdout, lambda x: blake2b_32(bytes(x)).digest())
        return cls()


class Sha256Instruction(MichelsonInstruction, prim='SHA256'):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        execute_hash(cls.prim, stack, stdout, lambda x: sha256(bytes(x)).digest())
        return cls()


class Sha512Instruction(MichelsonInstruction, prim='SHA512'):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        execute_hash(cls.prim, stack, stdout, lambda x: sha512(bytes(x)).digest())
        return cls()


class Sha3Instruction(MichelsonInstruction, prim='SHA3'):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        execute_hash(cls.prim, stack, stdout, lambda x: sha3.sha3_256(bytes(x)).digest())
        return cls()


class KeccakInstruction(MichelsonInstruction, prim='KECCAK'):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        execute_hash(cls.prim, stack, stdout, lambda x: sha3.keccak_256(bytes(x)).digest())
        return cls()


class CheckSignatureInstruction(MichelsonInstruction, prim='CHECK_SIGNATURE'):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        pk, sig, msg = cast(Tuple[KeyType, SignatureType, BytesType], stack.pop3())
        pk.assert_type_equal(KeyType)
        sig.assert_type_equal(SignatureType)
        msg.assert_type_equal(BytesType)
        key = Key.from_encoded_key(str(pk))
        try:
            key.verify(signature=str(sig), message=bytes(msg))
        except ValueError:
            res = BoolType(False)
        else:
            res = BoolType(True)
        stack.push(res)
        stdout.append(format_stdout(cls.prim, [pk, sig, msg], [res]))
        return cls()


class HashKeyInstruction(MichelsonInstruction, prim='HASH_KEY'):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        a = cast(KeyType, stack.pop1())
        a.assert_type_equal(KeyType)
        key = Key.from_encoded_key(str(a))
        res = KeyHashType.from_value(key.public_key_hash())
        stack.push(res)
        stdout.append(format_stdout(cls.prim, [a], [res]))
        return cls()


class PairingCheckInstruction(MichelsonInstruction, prim='PAIRING_CHECK'):

    @classmethod
    def execute(cls, stack: 'MichelsonStack', stdout: List[str], context: AbstractContext):
        points = cast(ListType, stack.pop1())
        points.assert_type_equal(ListType.create_type(
            args=[PairType.create_type(args=[BLS12_381_G1Type, BLS12_381_G2Type])]))
        prod = FQ12.one()
        for pair in points:
            g1, g2 = tuple(iter(pair))  # type: BLS12_381_G1Type, BLS12_381_G2Type
            prod = prod * bls12_381.pairing(g2.to_point(), g1.to_point())
        res = BoolType.from_value(FQ12.one() == prod)
        stack.push(res)
        stdout.append(format_stdout(cls.prim, [points], [res]))
        return cls()


class SaplingEmptyStateInstruction(MichelsonInstruction, prim='SAPLING_EMPTY_STATE', args_len=1):

    @classmethod
    def execute(cls, stack: MichelsonStack, stdout: List[str], context: AbstractContext):
        memo_size = cls.args[0].get_int()
        res = SaplingStateType.empty(memo_size)
        res.attach_context(context)
        stack.push(res)
        stdout.append(format_stdout(cls.prim, [], [res], memo_size))
        return cls()


class SaplingVerifyUpdateInstruction(MichelsonInstruction, prim='SAPLING_VERIFY_UPDATE'):
    pass
