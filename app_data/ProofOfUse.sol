// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ProofOfUse {
    uint256 public constant PAY_PER_DAY = 0.001 ether;
    uint256 public constant MIN_STAKE = 0.01 ether;
    uint256 public constant MAX_EVENTS_PER_DAY = 2;
    uint256 public constant HOLDBACK = 3 days;
    uint256 public constant SLASH = 0.005 ether;

    address public immutable oracle;
    address public immutable auditor;
    uint256 public pool;

    struct Stove {
        address operator;
        address sensor;            // key held by the stove's sensor; signs each day's raw data hash
        address[] households;
        bytes32 baseline;
        bytes32 head;
        uint256 stake;
    }
    struct Claim { bytes32 stoveId; uint64 releaseAt; bool done; }
    struct Day { bytes32 id; uint32 day; bytes32 rawHash; bytes32 prevHead; }

    mapping(bytes32 => Stove) public stoves;
    mapping(bytes32 => bool) public issued;
    mapping(bytes32 => bool) public usedEvent;
    mapping(address => uint256) public balance;
    Claim[] public claims;

    event DayIssued(bytes32 indexed stoveId, uint32 day, uint256 claimId);
    event Slashed(uint256 indexed claimId, uint256 amount);

    constructor(address _oracle, address _auditor) { oracle = _oracle; auditor = _auditor; }

    function fund() external payable { pool += msg.value; }

    function registerStove(bytes32 id, address[] calldata households, bytes32 baseline, address sensor) external payable {
        require(stoves[id].operator == address(0), "exists");
        require(msg.value >= MIN_STAKE && households.length > 0 && sensor != address(0), "stake/households/sensor");
        Stove storage s = stoves[id];
        s.operator = msg.sender;
        s.sensor = sensor;
        s.households = households;
        s.baseline = baseline;
        s.head = baseline;
        s.stake = msg.value;
    }

    function submitDay(Day calldata d, bytes32[] calldata eventHashes, bytes calldata sig) external {
        require(msg.sender == oracle, "oracle only");
        Stove storage s = stoves[d.id];
        require(s.operator != address(0), "no stove");
        require(eventHashes.length > 0 && eventHashes.length <= MAX_EVENTS_PER_DAY, "event cap");
        require(d.prevHead == s.head, "chain broken");
        // the sensor signed (stove, day, raw data hash, previous head): edited data or a forged signer fails here
        bytes32 digest = keccak256(abi.encode(d.id, d.day, d.rawHash, d.prevHead));
        require(_signer(digest, sig) == s.sensor, "bad sensor signature");
        bytes32 key = keccak256(abi.encode(d.id, d.day));
        require(!issued[key], "day already issued");
        issued[key] = true;
        for (uint256 i; i < eventHashes.length; i++) {
            require(!usedEvent[eventHashes[i]], "duplicate event");
            usedEvent[eventHashes[i]] = true;
        }
        s.head = keccak256(abi.encodePacked(d.prevHead, d.rawHash));   // contract computes the new head
        claims.push(Claim(d.id, uint64(block.timestamp + HOLDBACK), false));
        emit DayIssued(d.id, d.day, claims.length - 1);
    }

    function _signer(bytes32 digest, bytes calldata sig) internal pure returns (address) {
        require(sig.length == 65, "bad sig length");
        bytes32 r = bytes32(sig[0:32]);
        bytes32 sv = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        require(v == 27 || v == 28, "bad v");
        require(uint256(sv) <= 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0, "bad s");
        bytes32 ethDigest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", digest));
        address a = ecrecover(ethDigest, v, r, sv);
        require(a != address(0), "bad signature");
        return a;
    }

    function challenge(uint256 claimId) external {
        require(msg.sender == auditor, "auditor only");
        Claim storage c = claims[claimId];
        require(!c.done && block.timestamp < c.releaseAt, "closed");
        c.done = true;
        Stove storage s = stoves[c.stoveId];
        uint256 cut = s.stake < SLASH ? s.stake : SLASH;
        s.stake -= cut;
        pool += cut;
        emit Slashed(claimId, cut);
    }

    function release(uint256 claimId) external {
        Claim storage c = claims[claimId];
        require(!c.done && block.timestamp >= c.releaseAt, "not releasable");
        require(pool >= PAY_PER_DAY, "pool empty");
        c.done = true;
        pool -= PAY_PER_DAY;
        Stove storage s = stoves[c.stoveId];
        uint256 share = PAY_PER_DAY / s.households.length;
        for (uint256 i; i < s.households.length; i++) balance[s.households[i]] += share;
    }

    function withdraw() external {
        uint256 amt = balance[msg.sender];
        balance[msg.sender] = 0;
        (bool ok, ) = msg.sender.call{value: amt}("");
        require(ok, "send failed");
    }
}
