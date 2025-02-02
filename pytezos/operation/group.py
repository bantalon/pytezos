from pprint import pformat
from typing import List, Optional

from pytezos.crypto.key import blake2b_32
from pytezos.operation.content import ContentMixin
from pytezos.operation.forge import forge_operation_group
from pytezos.operation.fees import calculate_fee, default_fee, default_gas_limit, default_storage_limit
from pytezos.operation.result import OperationResult
from pytezos.rpc.errors import RpcError
from pytezos.crypto.encoding import base58_encode
from pytezos.michelson.forge import forge_base58
from pytezos.context.mixin import ContextMixin
from pytezos.context.impl import ExecutionContext
from pytezos.jupyter import get_class_docstring

validation_passes = {
    'endorsement': 0,
    'proposal': 1,
    'ballot': 1,
    'seed_nonce_revelation': 2,
    'double_endorsement_evidence': 2,
    'double_baking_evidence': 2,
    'activate_account': 2,
    'reveal': 3,
    'transaction': 3,
    'origination': 3,
    'delegation': 3
}


class OperationGroup(ContextMixin, ContentMixin):
    """ Operation group representation: contents (single or multiple), signature, other fields,
    and also useful helpers for filling with precise fees, signing, forging, and injecting.
    """

    def __init__(self, context: ExecutionContext,
                 contents=None, protocol=None, chain_id=None, branch=None, signature=None):
        super(OperationGroup, self).__init__(context=context)
        self.contents = contents or []
        self.protocol = protocol
        self.chain_id = chain_id
        self.branch = branch
        self.signature = signature

    def __repr__(self):
        res = [
            super(OperationGroup, self).__repr__(),
            '\nPayload',
            pformat(self.json_payload()),
            '\nHelpers',
            get_class_docstring(self.__class__)
        ]
        return '\n'.join(res)

    def _spawn(self, **kwargs):
        return OperationGroup(
            context=self.context,
            contents=kwargs.get('contents', self.contents.copy()),
            protocol=kwargs.get('protocol', self.protocol),
            chain_id=kwargs.get('chain_id', self.chain_id),
            branch=kwargs.get('branch', self.branch),
            signature=kwargs.get('signature', self.signature)
        )

    def json_payload(self) -> dict:
        """ Get json payload used for the preapply.
        """
        return {
            'protocol': self.protocol,
            'branch': self.branch,
            'contents': self.contents,
            'signature': self.signature
        }

    def binary_payload(self) -> bytes:
        """ Get binary payload used for injection/hash calculation.
        """
        if not self.signature:
            raise ValueError('Not signed')

        return bytes.fromhex(self.forge()) + forge_base58(self.signature)

    def operation(self, content):
        """ Create new operation group with extra content added.

        :param content: Kind-specific operation body
        :rtype: OperationGroup
        """
        return self._spawn(contents=self.contents + [content])

    def fill(self, counter=None, branch_offset=50):
        """ Try to fill all fields left unfilled, use approximate fees
        (not optimal, use `autofill` to simulate operation and get precise values).

        :param counter: Override counter value (for manual handling)
        :param branch_offset: select head~offset block as branch, where offset is in range (0, 60)
        :rtype: OperationGroup
        """
        assert 0 < branch_offset < 60, f'branch offset has to be in range (0, 60)'
        chain_id = self.chain_id or self.context.get_chain_id()
        branch = self.branch or self.shell.blocks[-branch_offset].hash()
        protocol = self.protocol or self.shell.head.header()['protocol']
        source = self.key.public_key_hash()

        if counter is not None:
            self.context.set_counter(counter)

        replace_map = {
            'pkh': source,
            'source': source,
            'delegate': source,  # self registration
            'counter': lambda x: str(self.context.get_counter()),
            'secret': lambda x: self.key.activation_code,
            'period': lambda x: str(self.shell.head.voting_period()),
            'public_key': lambda x: self.key.public_key(),
            'fee': lambda x: str(default_fee(x)),
            'gas_limit': lambda x: str(default_gas_limit(x)),
            'storage_limit': lambda x: str(default_storage_limit(x)),
        }

        def fill_content(content):
            content = content.copy()
            for k, v in replace_map.items():
                if content.get(k) in ['', '0']:
                    content[k] = v(content) if callable(v) else v
            return content

        return self._spawn(
            contents=list(map(fill_content, self.contents)),
            protocol=protocol,
            chain_id=chain_id,
            branch=branch
        )

    def run(self, block_id='head'):
        """ Simulate operation without signature checks.

        :param block_id: Specify a level at which this operation should be applied (default is head)
        :returns: RPC response from `run_operation`
        """
        return self.shell.blocks[block_id].helpers.scripts.run_operation.post({
            'operation': {
                'branch': self.branch,
                'contents': self.contents,
                'signature': base58_encode(b'0' * 64, b'sig').decode()
            },
            'chain_id': self.chain_id
        })

    def forge(self, validate=True):
        """ Convert json representation of the operation group into bytes.

        :param validate: Forge remotely also and compare results, default is True
        :returns: Hex string
        """
        payload = {
            'branch': self.branch,
            'contents': self.contents
        }
        local_data = forge_operation_group(payload).hex()

        if validate:
            remote_data = self.shell.blocks[self.branch].helpers.forge.operations.post(payload)
            if local_data != remote_data:
                raise ValueError(f'Local forge result differs from remote one:\n\n{local_data}\n\n{remote_data}')

        return local_data

    def autofill(self, gas_reserve=100, counter: Optional[int] = None, branch_offset=50):
        """ Fill the gaps and then simulate the operation in order to calculate fee, gas/storage limits.

        :param gas_reserve: Add a safe reserve for gas limit (default is 100)
        :param counter: Override counter value (for manual handling)
        :param branch_offset: select head~offset block as branch, where offset is in range (0, 60)
        :rtype: OperationGroup
        """
        opg = self.fill(counter=counter, branch_offset=branch_offset)
        opg_with_metadata = opg.run()
        if not OperationResult.is_applied(opg_with_metadata):
            raise RpcError.from_errors(OperationResult.errors(opg_with_metadata))

        extra_size = (32 + 64) // len(opg.contents) + 1  # size of serialized branch and signature)

        def fill_content(content):
            if validation_passes[content['kind']] == 3:
                consumed_gas = OperationResult.consumed_gas(content) + gas_reserve
                paid_storage_size_diff = OperationResult.paid_storage_size_diff(content)
                burned = OperationResult.burned(content)
                fee = calculate_fee(content, consumed_gas, extra_size)
                content.update(
                    gas_limit=str(consumed_gas + gas_reserve),
                    storage_limit=str(paid_storage_size_diff + burned),
                    fee=str(fee)
                )

            content.pop('metadata')
            return content

        opg.contents = list(map(fill_content, opg_with_metadata['contents']))
        return opg

    def sign(self):
        """ Sign the operation group with the key specified by `using`.

        :rtype: OperationGroup
        """
        validation_pass = validation_passes[self.contents[0]['kind']]
        if any(map(lambda x: validation_passes[x['kind']] != validation_pass, self.contents)):
            raise ValueError('Mixed validation passes')

        if validation_pass == 0:
            chain_watermark = bytes.fromhex(self.shell.chains.main.watermark())
            watermark = b'\x02' + chain_watermark
        else:
            watermark = b'\x03'

        message = watermark + bytes.fromhex(self.forge())
        signature = self.key.sign(message=message, generic=True)

        return self._spawn(signature=signature)

    def hash(self) -> str:
        """ Calculate the Base58 encoded operation group hash.
        """
        hash_digest = blake2b_32(self.binary_payload()).digest()
        return base58_encode(hash_digest, b'o').decode()

    def preapply(self):
        """ Preapply signed operation group.

        :returns: RPC response from `preapply`
        """
        if not self.signature:
            raise ValueError('Not signed')

        return self.shell.head.helpers.preapply.operations.post(
            operations=[self.json_payload()])[0]

    def inject(self, _async=True, preapply=True, check_result=True,
               num_blocks_wait=5, time_between_blocks: Optional[int] = None):
        """ Inject the signed operation group.

        :param _async: do not wait for operation inclusion (default is True)
        :param preapply: do a preapply before injection
        :param check_result: raise RpcError in case operation is applied but has runtime errors
        :param num_blocks_wait: number of blocks to wait for injection
        :param time_between_blocks: override the corresponding parameter from constants
        :returns: operation group with metadata (raw RPC response)
        """
        self.context.reset()
        if preapply:
            opg_with_metadata = self.preapply()
            if not OperationResult.is_applied(opg_with_metadata):
                raise RpcError.from_errors(OperationResult.errors(opg_with_metadata))

        opg_hash = self.shell.injection.operation.post(operation=self.binary_payload(), _async=False)

        if _async:
            return {
                'chain_id': self.chain_id,
                'hash': opg_hash,
                **self.json_payload()
            }
        else:
            for i in range(num_blocks_wait):
                self.shell.wait_next_block(time_between_blocks=time_between_blocks)
                try:
                    pending_opg = self.shell.mempool.pending_operations[opg_hash]
                    if not OperationResult.is_applied(pending_opg):
                        raise RpcError.from_errors(OperationResult.errors(pending_opg))
                    print(f'Still in mempool: {opg_hash}')
                except StopIteration:
                    res = self.shell.blocks[-(i + 1):].find_operation(opg_hash)
                    if check_result:
                        if not OperationResult.is_applied(res):
                            raise RpcError.from_errors(OperationResult.errors(res))
                    return res

        raise TimeoutError(opg_hash)

    def result(self) -> List[OperationResult]:
        """ Parse the preapply result.

        :rtype: List[OperationResult]
        """
        return OperationResult.from_operation_group(self.preapply())
