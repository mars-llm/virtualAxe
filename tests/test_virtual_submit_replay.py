import importlib.util
import asyncio
import json
import struct
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT_DIR / "scripts" / "stratum_replay.py"


def load_module():
    spec = importlib.util.spec_from_file_location("stratum_replay_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def nerdnos_direct_header(module, job, submission, extranonce_1):
    """Mirror NerdNos bm_job header bytes after its source-native V1 job builder."""
    rolled_version = job.version ^ submission.version_bits
    prev_hash = module.reverse_endianness_per_word(bytes.fromhex(job.prev_block_hash))
    merkle_root = module.calculate_merkle_root(job, extranonce_1, submission.extranonce_2)
    return b"".join(
        [
            struct.pack("<I", rolled_version),
            prev_hash,
            merkle_root,
            struct.pack("<I", submission.ntime),
            struct.pack("<I", job.target),
            struct.pack("<I", submission.nonce),
        ]
    )


def test_known_low_difficulty_submit_validates_without_host_search():
    module = load_module()
    submission = module.ShareSubmission(
        username="bc1qvirtualaxereplay.worker",
        job_id=module.DEFAULT_JOB.job_id,
        extranonce_2="00000000",
        ntime=module.DEFAULT_JOB.ntime,
        nonce=0x0A029ED1,
        version_bits=0,
    )

    validation = module.validate_submission(submission, difficulty=1.0)

    assert validation.accepted is True
    assert validation.reason == "accepted"
    assert int(validation.difficulty) == 683
    assert validation.rolled_version == module.DEFAULT_JOB.version
    assert validation.hash_hex.startswith("000000")


def test_replay_rejects_submit_for_wrong_job_id():
    module = load_module()
    submission = module.ShareSubmission(
        username="bc1qvirtualaxereplay.worker",
        job_id="stale-job",
        extranonce_2="00000000",
        ntime=module.DEFAULT_JOB.ntime,
        nonce=0x0A029ED1,
        version_bits=0,
    )

    validation = module.validate_submission(submission, difficulty=1.0)

    assert validation.accepted is False
    assert validation.reason == "job-not-found"


def test_bitronics_3334_captured_submit_validates_with_eight_byte_extranonce():
    module = load_module()
    bitronics_job = module.ReplayJob(
        job_id="69a1f6420003d580",
        prev_block_hash="2cde84c89111becc7246c32a09e012730f4a1e170000ece60000000000000000",
        coinbase_1=(
            "01000000010000000000000000000000000000000000000000000000000000000000000000"
            "ffffffff3403e1720e00042fb4ef69047af7f1040c"
        ),
        coinbase_2=(
            "0a636b706f6f6c102f626974726f6e6963732d706f6f6c2fffffffff029690b81200000000225120"
            "ffeeddccbbaa99887766554433221100ffeeddccbbaa998877665544332211000000000000000000"
            "266a24aa21a9edfc3cc91ec6c73d2f663a28124457c38ddc6991d9b3b3e453c769fcd356f97782"
            "00000000"
        ),
        merkle_branches=(
            "1d7e2e17fd6bf00716bf05278bf7b93d7ca440dae25395a3389bfb08a78aea69",
            "5986495697ebf1cc3e58ed1b333906f85bd670dbbfbe6b341820b54a2e7f9369",
            "905fe98b172d20e0d6077d22b77d074072efc297176aaa3f7ad967d9f352cdbf",
            "e11d4e5a3310006a31e01258b9602cbf1886c34477fe2b1cd5ee40bbe46903d6",
            "bd3246aa17e1d2ac8fb97cd77cf61277953d778bf7eece34acc2b730137d709c",
            "f4763fed31c7ffb568dd6f1e044d0db90547e9c2ca6a37bdd8cb2a378abd2b42",
            "b982deb3814c28494d4ac16385fb6f5b3389bc6d70dd23bf1e9f46e3a5648055",
            "9c49832e2e815f980d17b919bf1221fda419aa01632310ebb89d759fd973c3ad",
            "d7343a083f83711627d5f8196d238000ec5dd1b0a6e5ef9fbf419f942d2766ce",
            "3a44a84b1ad9e71b68946b6b020c82569445bfe6019b31715ccd95a07ae75a8c",
            "935ca7d7607640bfbf21520e171df260602e63c47405bdd05421fd1c0022ff2e",
            "2f50b9cc3364b15aacab3e8baa19cb20e4e7809131bbfc7368672995987725e8",
            "7b9f3567d68d11b4bde3ac8976c2798962fe86c3774425b17fd9ab36964cd912",
        ),
        version=0x20000000,
        target=0x17021369,
        ntime=0x69EFB42E,
    )
    submission = module.ShareSubmission(
        username="bc1qvirtualaxereplayfixture.gamma.bitronics3334.20260427-204537b",
        job_id=bitronics_job.job_id,
        extranonce_2="0100000000000000",
        ntime=bitronics_job.ntime,
        nonce=0x000C9DD7,
        version_bits=0x00002000,
    )

    validation = module.validate_submission(
        submission,
        job=bitronics_job,
        extranonce_1="d30ea269",
        extranonce_2_size=8,
        difficulty=0.0005,
    )

    assert validation.accepted is True
    assert validation.reason == "accepted"
    assert validation.difficulty > 0.0005
    assert validation.rolled_version == 0x20002000
    assert validation.hash_hex == "000004877cffa545293c44e0e570a17b00b5a64728322387d798e422701db390"


def test_latest_nerdnos_public_pool_submit_matches_source_native_header():
    module = load_module()
    public_pool_job = module.ReplayJob(
        job_id="236e82d",
        prev_block_hash="6d7c8e1b6602dc0973cc7cbebd743ec7c8057a2e0000f6080000000000000000",
        coinbase_1=(
            "02000000010000000000000000000000000000000000000000000000000000000000000000"
            "ffffffff1b03df7b0e5075626c69632d506f6f6c"
        ),
        coinbase_2=(
            "ffffffff029a40a112000000001976a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac"
            "0000000000000000266a24aa21a9edfbbb36bca14c94919e39775406dc7f59ec29163c9bde3ccda"
            "0d802da236cfd3f00000000"
        ),
        merkle_branches=(
            "cf422cce8196f7711843d495a40b139f1924d09cd1ee7f915e7c9db3ff06fb02",
            "a60a2dd2d3d842210d76c45c341a125365a4ae671d454d642296484536598cd6",
            "0fad2e9ba0f6f4dabc3dbda8da8addcb846b96dc034314a5b66d2ac0eb19109a",
            "4acfbed1fd099afc4b064c6e885a3026f9c8d80b7938f16610958304e7622308",
            "01fe965930c937ca2d8179e06cae1f1f7adf79cb85856dd4491cc1f3192a9c08",
            "8d659706916d8d172e3a936d989c62565599aad0da3a23d4afa479fd0e2188f2",
            "34dd45ca4873aba56b99eaf64dc2f9452cdd7bcc342d1e9ed968c218d8514762",
        ),
        version=0x20000000,
        target=0x17021FF0,
        ntime=0x6A0475A5,
    )
    submission = module.ShareSubmission(
        username="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa.vagpubrnbli",
        job_id=public_pool_job.job_id,
        extranonce_2="0000000000000001",
        ntime=public_pool_job.ntime,
        nonce=0x000297FC,
        version_bits=0x00004000,
    )

    validation = module.validate_submission(
        submission,
        job=public_pool_job,
        extranonce_1="14e57708",
        extranonce_2_size=8,
        difficulty=0.0001,
    )

    assert validation.accepted is True
    assert validation.difficulty > 0.0001
    assert validation.rolled_version == 0x20004000
    assert validation.hash_hex == "00000f60e9c61d63dfd25227230475264e83d39d2db20064d4d784727bb3d2b6"
    assert validation.header_hex == nerdnos_direct_header(module, public_pool_job, submission, "14e57708").hex()


def test_latest_nerdnos_nerdminers_submit_matches_source_native_header():
    module = load_module()
    nerdminers_job = module.ReplayJob(
        job_id="6a0378910000084c",
        prev_block_hash="5a6aea539ea303082dd72dbfff4e585dba6e7dc7000084070000000000000000",
        coinbase_1=(
            "01000000010000000000000000000000000000000000000000000000000000000000000000"
            "ffffffff3803e07b0e00040a79046a045b5a2d020c"
        ),
        coinbase_2=(
            "0a636b706f6f6c142f6d696e6564206279206e6572646d696e65722fffffffff02e157a91200000000"
            "1976a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac0000000000000000266a24aa21"
            "a9ed0e83e4a428ddeb7256de7d5b1b375e4b586927addce7fc94c5dc64b816e4ae1600000000"
        ),
        merkle_branches=(
            "f8dc68505c63ea8ef0d9ad3a5e4077572e2bec29f22915c7f1a32dbf12c28fcd",
            "26bc2719d424de3454eb5d16daaf6f83f46e89392791e274133203334166228a",
            "b51ac27b5d473453c196ae2952ef409d9455e3933204615d93c184af7e2afa02",
            "b80886467adaa3632ee260fbe39e684b2e0cecc9dea9dbba830dee222e6ad0a8",
            "579db32a4e1da8d6faff88348d7d144eed2a59667e84eff59bf50a0ee4bbdfe6",
            "5b29ee3a10bfe88819bacc7c653794843bda3cca23c1e9abad1d439aa978a28f",
            "ef7b5f119a4e1ebbe876838a34f9f8c61fb67a533294fb9f31932d5185b2cdab",
            "ed8a06d948b102f11100d3b6a6e2c6f6550f0a878c7212b6eb750f31d2d76674",
            "e63eecd8356927ff3156e3f2b627f66517e27ae27d0b841886ead808a017b56a",
            "6c087c17cdfd6af4cee656b00c75223ca6fcafaf9b627d4e3bded8e6a3c0889b",
            "44320ca6dd9fd59b3dd2222760c4a1d342ed6297ee8b4d449617a36bfeb8119c",
            "fa8b6b2fe02fedbc6a3f50d872ef3942afaa939e42d08405e22d772b8fa45e52",
            "98a7643c3a7ea74fd46e5afebad6899a959750d5e251b3f3497ee242ef2fec6f",
        ),
        version=0x20000000,
        target=0x17021FF0,
        ntime=0x6A047909,
    )
    submission = module.ShareSubmission(
        username="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa.vagnerdrnbli",
        job_id=nerdminers_job.job_id,
        extranonce_2="000000000000000a",
        ntime=nerdminers_job.ntime,
        nonce=0x0003B53C,
        version_bits=0x00002000,
    )

    validation = module.validate_submission(
        submission,
        job=nerdminers_job,
        extranonce_1="ec3d4b6a",
        extranonce_2_size=8,
        difficulty=0.001,
    )

    assert validation.accepted is True
    assert validation.difficulty > 0.001
    assert validation.rolled_version == 0x20002000
    assert validation.hash_hex == "000001216d64257d3a439e3d9c99ecb0fd04001a52d6d186a254cd4cffe760a1"
    assert validation.header_hex == nerdnos_direct_header(module, nerdminers_job, submission, "ec3d4b6a").hex()


def test_nerdnos_publicpool_rejected_submit_was_valid_stale_work():
    module = load_module()
    public_pool_job = module.ReplayJob(
        job_id="2b4c09c",
        prev_block_hash="b4172a37025070188451a5f70996bfff6292431e0001f52d0000000000000000",
        coinbase_1=(
            "02000000010000000000000000000000000000000000000000000000000000000000000000"
            "ffffffff1b03ae7c0e5075626c69632d506f6f6c"
        ),
        coinbase_2=(
            "ffffffff02fa8da212000000001976a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac"
            "0000000000000000266a24aa21a9ed556bfd64f019d1b2fc86d00650a3099df96121a53ef5a"
            "cf6b8dd2549257f10c400000000"
        ),
        merkle_branches=(
            "28e637239514a6c3ef9c95be7863abf7deceafb5da2774f8186b5793271e940c",
            "9c4c9dbfb6b6515fa94dd644cc4b3104c28ad4d5fc21d8e58811398c03942263",
            "b432a46a683fb13d31b45031c1d3a8340daafc19279ce67e6560fc351c5151fe",
            "ca2cc2acc4a8e5dc760d14aa51b0b841f113c61d1cb211ac4d9349dc22a84cd3",
            "397d8853c95fed821798bb9bf0fc484e043b037a39775dc2c2a886917b4a2de6",
            "15a3351c0c1254740389fca1a31ad24f66dd179e6e457c781c9d854df99e2c7d",
            "a3c7d108ad581ff26967285f17ceb2cfe2a375cf0ce80aaf3c3e2512529968a0",
            "7e4b3ea2efc95741ae55dd68f961e9e697d056b09c7f5b3f53431be7519ce6d2",
        ),
        version=0x20000000,
        target=0x17021FF0,
        ntime=0x6A063824,
    )
    submission = module.ShareSubmission(
        username="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa.vagpubrwyag",
        job_id=public_pool_job.job_id,
        extranonce_2="0000000000000015",
        ntime=public_pool_job.ntime,
        nonce=0x00130343,
        version_bits=0x00002000,
    )

    validation = module.validate_submission(
        submission,
        job=public_pool_job,
        extranonce_1="de5e1909",
        extranonce_2_size=8,
        difficulty=0.001,
    )

    assert validation.accepted is True
    assert validation.difficulty > 0.001
    assert validation.rolled_version == 0x20002000
    assert validation.hash_hex == "0000021da08251469cca0368fd804c653fc76bb031849fb38e4f2412852bf8db"
    assert validation.header_hex == nerdnos_direct_header(module, public_pool_job, submission, "de5e1909").hex()


def test_replay_server_completes_when_submit_is_accepted():
    module = load_module()

    async def run_client():
        username = "bc1qvirtualaxereplay.worker"
        replay = module.ReplayServer(
            difficulty=1.0,
            username=username,
            timeout=1.0,
            extranonce_1=module.DEFAULT_EXTRANONCE1,
            extranonce_2_size=module.DEFAULT_EXTRANONCE2_SIZE,
        )
        server = await asyncio.start_server(replay.handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            wait_task = asyncio.create_task(replay.wait_for_submit())

            async def send(payload):
                writer.write((json.dumps(payload) + "\n").encode("utf-8"))
                await writer.drain()

            await send({"id": 1, "method": "mining.subscribe", "params": []})
            await send({"id": 2, "method": "mining.authorize", "params": [username, "x"]})
            await send(
                {
                    "id": 3,
                    "method": "mining.submit",
                    "params": [
                        username,
                        module.DEFAULT_JOB.job_id,
                        "00000000",
                        f"{module.DEFAULT_JOB.ntime:08x}",
                        "0a029ed1",
                        "00000000",
                    ],
                }
            )
            validation, submission = await wait_task
            responses = [
                json.loads((await asyncio.wait_for(reader.readline(), timeout=1.0)).decode("utf-8"))
                for _ in range(5)
            ]
            writer.close()
            await writer.wait_closed()
            return validation, submission, responses

    validation, submission, responses = asyncio.run(run_client())

    assert validation.accepted is True
    assert submission.nonce == 0x0A029ED1
    assert any(response.get("id") == 3 and response.get("result") is True for response in responses)
