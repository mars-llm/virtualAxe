#!/usr/bin/env python3
import argparse
import asyncio
import json
import struct
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


TRUE_DIFF_ONE = int("00000000ffff0000000000000000000000000000000000000000000000000000", 16)
DEFAULT_EXTRANONCE1 = "01000000"
DEFAULT_EXTRANONCE2_SIZE = 4
DEFAULT_VERSION_MASK = 0x1FFFE000


@dataclass(frozen=True)
class ReplayJob:
    job_id: str
    prev_block_hash: str
    coinbase_1: str
    coinbase_2: str
    merkle_branches: tuple[str, ...]
    version: int
    target: int
    ntime: int

    def notify_params(self, *, clean_jobs: bool = True) -> list[Any]:
        return [
            self.job_id,
            self.prev_block_hash,
            self.coinbase_1,
            self.coinbase_2,
            list(self.merkle_branches),
            f"{self.version:08x}",
            f"{self.target:08x}",
            f"{self.ntime:08x}",
            clean_jobs,
        ]


@dataclass(frozen=True)
class ShareSubmission:
    username: str
    job_id: str
    extranonce_2: str
    ntime: int
    nonce: int
    version_bits: int


@dataclass(frozen=True)
class ShareValidation:
    accepted: bool
    reason: str
    difficulty: float
    header_hex: str
    rolled_version: int
    hash_hex: str = ""


DEFAULT_JOB = ReplayJob(
    job_id="virtualaxe-replay-01",
    prev_block_hash="0c859545a3498373a57452fac22eb7113df2a465000543520000000000000000",
    coinbase_1=(
        "01000000010000000000000000000000000000000000000000000000000000000000000000"
        "ffffffff4b0389130cfabe6d6d5cbab26a2599e92916edec5657a94a0708ddb970"
        "f5c45b5d12905085617eff8e"
    ),
    coinbase_2=(
        "31650707758de07b010000000000001cfd7038212f736c7573682f000000000379"
        "ad0c2a000000001976a9147c154ed1dc59609e3d26abb2df2ea3d587cd8c4188ac"
        "00000000000000002c6a4c2952534b424c4f434b3ae725d3994b811572c1f345deb"
        "98b56b465ef8e153ecbbd27fa37bf1b005161380000000000000000266a24aa21a9"
        "ed63b06a7946b190a3fda1d76165b25c9b883bcc6621b040773050ee2a1bb18f18"
        "00000000"
    ),
    merkle_branches=(
        "2b77d9e413e8121cd7a17ff46029591051d0922bd90b2b2a38811af1cb57a2b2",
        "5c8874cef00f3a233939516950e160949ef327891c9090467cead995441d22c5",
        "2d91ff8e19ac5fa69a40081f26c5852d366d608b04d2efe0d5b65d111d0d8074",
        "0ae96f609ad2264112a0b2dfb65624bedbcea3b036a59c0173394bba3a74e887",
        "e62172e63973d69574a82828aeb5711fc5ff97946db10fc7ec32830b24df7bde",
        "adb49456453aab49549a9eb46bb26787fb538e0a5f656992275194c04651ec97",
        "a7bc56d04d2672a8683892d6c8d376c73d250a4871fdf6f57019bcc737d6d2c2",
        "d94eceb8182b4f418cd071e93ec2a8993a0898d4c93bc33d9302f60dbbd0ed10",
        "5ad7788b8c66f8f50d332b88a80077ce10e54281ca472b4ed9bbbbcb6cf99083",
        "9f9d784b33df1b3ed3edb4211afc0dc1909af9758c6f8267e469f5148ed04809",
        "48fd17affa76b23e6fb2257df30374da839d6cb264656a82e34b350722b05123",
        "c4f5ab01913fc186d550c1a28f3f3e9ffaca2016b961a6a751f8cca0089df924",
        "cff737e1d00176dd6bbfa73071adbb370f227cfb5fba186562e4060fcec877e1",
    ),
    version=0x20000004,
    target=0x1705AE3A,
    ntime=0x647025B5,
)


def double_sha256(data: bytes) -> bytes:
    return sha256(sha256(data).digest()).digest()


def reverse_endianness_per_word(data: bytes) -> bytes:
    if len(data) % 4 != 0:
        raise ValueError("word-reversal input length must be divisible by 4")
    return b"".join(data[index : index + 4][::-1] for index in range(0, len(data), 4))


def calculate_coinbase_tx_hash(job: ReplayJob, extranonce_1: str, extranonce_2: str) -> bytes:
    coinbase = bytes.fromhex(job.coinbase_1 + extranonce_1 + extranonce_2 + job.coinbase_2)
    return double_sha256(coinbase)


def calculate_merkle_root(job: ReplayJob, extranonce_1: str, extranonce_2: str) -> bytes:
    root = calculate_coinbase_tx_hash(job, extranonce_1, extranonce_2)
    for branch in job.merkle_branches:
        root = double_sha256(root + bytes.fromhex(branch))
    return root


def build_header(job: ReplayJob, submission: ShareSubmission, extranonce_1: str) -> bytes:
    rolled_version = job.version ^ submission.version_bits
    prev_hash = reverse_endianness_per_word(bytes.fromhex(job.prev_block_hash))
    merkle_root = calculate_merkle_root(job, extranonce_1, submission.extranonce_2)
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


def share_difficulty(header: bytes) -> float:
    digest = double_sha256(header)
    value = int.from_bytes(digest, "little")
    if value == 0:
        return float("inf")
    return TRUE_DIFF_ONE / value


def share_hash_hex(header: bytes) -> str:
    return double_sha256(header)[::-1].hex()


def parse_submit_params(params: list[Any]) -> ShareSubmission:
    if len(params) != 6:
        raise ValueError(f"expected 6 mining.submit params, got {len(params)}")
    return ShareSubmission(
        username=str(params[0]),
        job_id=str(params[1]),
        extranonce_2=str(params[2]),
        ntime=int(str(params[3]), 16),
        nonce=int(str(params[4]), 16),
        version_bits=int(str(params[5]), 16),
    )


def validate_submission(
    submission: ShareSubmission,
    *,
    job: ReplayJob = DEFAULT_JOB,
    extranonce_1: str = DEFAULT_EXTRANONCE1,
    extranonce_2_size: int = DEFAULT_EXTRANONCE2_SIZE,
    difficulty: float,
    username: str | None = None,
) -> ShareValidation:
    if username is not None and submission.username != username:
        return ShareValidation(False, "unauthorized-user", 0.0, "", job.version ^ submission.version_bits)
    if submission.job_id != job.job_id:
        return ShareValidation(False, "job-not-found", 0.0, "", job.version ^ submission.version_bits)
    if submission.ntime != job.ntime:
        return ShareValidation(False, "ntime-mismatch", 0.0, "", job.version ^ submission.version_bits)
    if len(submission.extranonce_2) != extranonce_2_size * 2:
        return ShareValidation(False, "extranonce2-size-mismatch", 0.0, "", job.version ^ submission.version_bits)

    header = build_header(job, submission, extranonce_1)
    share_diff = share_difficulty(header)
    accepted = share_diff >= difficulty
    reason = "accepted" if accepted else "above-target"
    return ShareValidation(accepted, reason, share_diff, header.hex(), job.version ^ submission.version_bits, share_hash_hex(header))


class ReplayServer:
    def __init__(
        self,
        *,
        difficulty: float,
        username: str,
        timeout: float,
        extranonce_1: str,
        extranonce_2_size: int,
    ) -> None:
        self.difficulty = difficulty
        self.username = username
        self.timeout = timeout
        self.extranonce_1 = extranonce_1
        self.extranonce_2_size = extranonce_2_size
        self.accepted: ShareValidation | None = None
        self.accepted_submission: ShareSubmission | None = None
        self._accepted_future: asyncio.Future[tuple[ShareValidation, ShareSubmission]] | None = None
        self.started_at = time.time()
        self._payload_emitted = False

    def accepted_payload(self, validation: ShareValidation, submission: ShareSubmission) -> dict[str, Any]:
        return {
            "status": "accepted",
            "durationSeconds": round(time.time() - self.started_at, 3),
            "assignedDifficulty": self.difficulty,
            "shareDifficulty": validation.difficulty,
            "rolledVersion": f"{validation.rolled_version:08x}",
            "jobId": DEFAULT_JOB.job_id,
            "header": validation.header_hex,
            "hash": validation.hash_hex,
            "submission": {
                "username": submission.username,
                "jobId": submission.job_id,
                "extranonce2": submission.extranonce_2,
                "ntime": f"{submission.ntime:08x}",
                "nonce": f"{submission.nonce:08x}",
                "versionBits": f"{submission.version_bits:08x}",
            },
        }

    def emit_payload(self, payload: dict[str, Any]) -> None:
        if self._payload_emitted:
            return
        print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
        self._payload_emitted = True

    async def send_json(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        await writer.drain()

    async def send_work(self, writer: asyncio.StreamWriter) -> None:
        await self.send_json(writer, {"id": None, "method": "mining.set_difficulty", "params": [self.difficulty]})
        await self.send_json(writer, {"id": None, "method": "mining.notify", "params": DEFAULT_JOB.notify_params()})

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        subscribed = False
        authorized = False
        work_sent = False
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                message = json.loads(line.decode("utf-8"))
                method = message.get("method")
                message_id = message.get("id")

                if method == "mining.configure":
                    await self.send_json(
                        writer,
                        {
                            "id": message_id,
                            "result": {
                                "version-rolling": True,
                                "version-rolling.mask": f"{DEFAULT_VERSION_MASK:08x}",
                            },
                            "error": None,
                        },
                    )
                elif method == "mining.subscribe":
                    subscribed = True
                    await self.send_json(
                        writer,
                        {
                            "id": message_id,
                            "result": [[["mining.set_difficulty", "1"], ["mining.notify", "1"]], self.extranonce_1, self.extranonce_2_size],
                            "error": None,
                        },
                    )
                elif method == "mining.authorize":
                    authorized = True
                    await self.send_json(writer, {"id": message_id, "result": True, "error": None})
                elif method == "mining.suggest_difficulty":
                    await self.send_json(writer, {"id": message_id, "result": True, "error": None})
                elif method == "mining.extranonce.subscribe":
                    await self.send_json(writer, {"id": message_id, "result": True, "error": None})
                elif method == "mining.submit":
                    submission = parse_submit_params(message.get("params", []))
                    validation = validate_submission(
                        submission,
                        extranonce_1=self.extranonce_1,
                        extranonce_2_size=self.extranonce_2_size,
                        difficulty=self.difficulty,
                        username=self.username,
                    )
                    await self.send_json(writer, {"id": message_id, "result": validation.accepted, "error": None if validation.accepted else validation.reason})
                    if validation.accepted:
                        self.accepted = validation
                        self.accepted_submission = submission
                        self.emit_payload(self.accepted_payload(validation, submission))
                        if self._accepted_future is not None and not self._accepted_future.done():
                            self._accepted_future.set_result((validation, submission))
                else:
                    await self.send_json(writer, {"id": message_id, "result": True, "error": None})

                if subscribed and authorized and not work_sent:
                    work_sent = True
                    await self.send_work(writer)
        finally:
            writer.close()
            await writer.wait_closed()

    async def wait_for_submit(self) -> tuple[ShareValidation, ShareSubmission]:
        if self.accepted is not None and self.accepted_submission is not None:
            return self.accepted, self.accepted_submission
        self._accepted_future = asyncio.get_running_loop().create_future()
        return await asyncio.wait_for(self._accepted_future, timeout=self.timeout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve deterministic low-difficulty Stratum work and validate guest mining.submit output.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--difficulty", type=float, default=0.001)
    parser.add_argument("--username", default="bc1qvirtualaxereplay.worker")
    parser.add_argument("--extranonce1", default=DEFAULT_EXTRANONCE1)
    parser.add_argument("--extranonce2-size", type=int, default=DEFAULT_EXTRANONCE2_SIZE)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()
    if args.extranonce2_size < 1:
        parser.error("--extranonce2-size must be positive")
    try:
        bytes.fromhex(args.extranonce1)
    except ValueError:
        parser.error("--extranonce1 must be hex")
    return args


async def async_main(args: argparse.Namespace) -> int:
    replay = ReplayServer(
        difficulty=args.difficulty,
        username=args.username,
        timeout=args.timeout,
        extranonce_1=args.extranonce1,
        extranonce_2_size=args.extranonce2_size,
    )
    server = await asyncio.start_server(replay.handle_client, args.host, args.port)
    async with server:
        replay.started_at = time.time()
        try:
            validation, submission = await replay.wait_for_submit()
            await asyncio.sleep(5.0)
        except asyncio.TimeoutError:
            payload = {
                "status": "timeout",
                "durationSeconds": round(time.time() - replay.started_at, 3),
                "assignedDifficulty": args.difficulty,
                "jobId": DEFAULT_JOB.job_id,
                "reason": "no accepted mining.submit before timeout",
            }
            replay.emit_payload(payload)
            return 124
        finally:
            server.close()
            await server.wait_closed()

    replay.emit_payload(replay.accepted_payload(validation, submission))
    return 0


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
