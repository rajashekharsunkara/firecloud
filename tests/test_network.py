import pytest
from pathlib import Path

from firecloud.network import Network
from firecloud.crypto import (
    derive_auth_token,
    derive_encryption_key,
    derive_hmac_key,
)
from firecloud.exceptions import NetworkKeyError


def test_network_create():
    passphrase = "my-secret-passphrase"
    net = Network.create(passphrase)
    
    assert len(net.key) == 32
    assert net.passphrase == passphrase
    assert len(net.network_id) == 16  # 8 bytes hex-encoded
    
    # Check that derived keys match
    assert net.auth_token == derive_auth_token(net.key)
    assert net.encryption_key == derive_encryption_key(net.key)
    assert net.hmac_key == derive_hmac_key(net.key)


def test_network_save_load(tmp_path):
    passphrase = "my-secret-passphrase"
    net = Network.create(passphrase)
    
    key_path = tmp_path / "network.key"
    net.save(key_path)
    
    # Load with same passphrase
    loaded_net = Network.load(key_path, passphrase)
    assert loaded_net.key == net.key
    assert loaded_net.network_id == net.network_id
    assert loaded_net.passphrase == passphrase


def test_network_save_explicit_passphrase(tmp_path):
    passphrase = "initial-passphrase"
    net = Network.create(passphrase)
    
    key_path = tmp_path / "network.key"
    new_passphrase = "different-passphrase"
    net.save(key_path, passphrase=new_passphrase)
    
    # Should not load with initial passphrase
    with pytest.raises(NetworkKeyError):
        Network.load(key_path, passphrase)
        
    # Should load with new passphrase
    loaded_net = Network.load(key_path, new_passphrase)
    assert loaded_net.key == net.key


def test_network_load_invalid_passphrase(tmp_path):
    passphrase = "correct-passphrase"
    net = Network.create(passphrase)
    
    key_path = tmp_path / "network.key"
    net.save(key_path)
    
    with pytest.raises(NetworkKeyError):
        Network.load(key_path, "wrong-passphrase")


def test_network_save_missing_passphrase(tmp_path):
    # Network created without passphrase in constructor
    net = Network(b"\x01" * 32)
    key_path = tmp_path / "network.key"
    
    with pytest.raises(ValueError, match="Passphrase required"):
        net.save(key_path)
