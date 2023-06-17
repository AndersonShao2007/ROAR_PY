from roar_py_interface import RoarPySensor, RoarPyRemoteSupportedSensorData, RoarPyRemoteSupportedSensorSerializationScheme
from ..base import RoarPyObjectWithRemoteMessage, register_object_with_remote_message
from typing import Any, TypeVar, Generic, Optional
import gymnasium as gym
from serde import serde
from dataclasses import dataclass
import pickle
import zlib
import asyncio
import copy

_ObsT = TypeVar("_ObsT")
class RoarPyRemoteSharedSensor(RoarPySensor[_ObsT], Generic[_ObsT]):
    async def receive_observation(self) -> Any:
        raise PermissionError("This sensor is shared, you cannot receive observation from it")
    
    def __setattr__(self, __name: str, __value: Any) -> None:
        raise PermissionError("Cannot set attribute on a shared actor")

    def __delattr__(self, __name: str) -> None:
        raise PermissionError("Cannot delete attribute on a shared actor")

    def close(self):
        pass

@serde
@dataclass
class RoarPyRemoteSensorObsInfo:
    name: Optional[str]
    control_timestep: float
    last_data: Optional[bytes]
    last_data_type: str
    obs_spec: Optional[bytes]
    is_closed: bool

    def get_obs_spec(self) -> Optional[gym.Space]:
        if self.obs_spec is None:
            return None
        return pickle.loads(zlib.decompress(self.obs_spec))
    
    def get_last_obs(self) -> Optional[RoarPyRemoteSupportedSensorData]:
        if self.last_data is None:
            return None

        assert self.last_data_type in RoarPyRemoteSupportedSensorData._supported_data_types, f"Unsupported data type {self.last_data_type}"
        
        last_data_type_real = RoarPyRemoteSupportedSensorData._supported_data_types[self.last_data_type]
        try:
            self._new_data = last_data_type_real.from_data(
                self.last_data,
                RoarPyRemoteSupportedSensorSerializationScheme.MSGPACK_COMPRESSED
            )
        except Exception as e:
            return None
    
    @staticmethod
    def from_sensor(sensor: RoarPySensor) -> "RoarPyRemoteSensorObsInfo":
        last_obs = sensor.get_last_observation()
        assert last_obs is not None and isinstance(last_obs, RoarPyRemoteSupportedSensorData)
        return RoarPyRemoteSensorObsInfo(
            name = sensor.name,
            control_timestep = sensor.control_timestep,
            last_data = last_obs.to_data(RoarPyRemoteSupportedSensorSerializationScheme.MSGPACK_COMPRESSED),
            last_data_type = last_obs.__class__.__name__,
            obs_spec = zlib.compress(pickle.dumps(sensor.get_gym_observation_spec(), protocol=pickle.DEFAULT_PROTOCOL)),
            is_closed = sensor.is_closed()
        )

@serde
@dataclass
class RoarPyRemoteSensorObsInfoRequest:
    close : bool

_ObsTClient = TypeVar("_ObsTClient", bound=RoarPyRemoteSupportedSensorData)

@register_object_with_remote_message(RoarPyRemoteSensorObsInfo, RoarPyRemoteSensorObsInfoRequest)
class RoarPyRemoteClientSensor(RoarPySensor[_ObsTClient], Generic[_ObsTClient], RoarPyObjectWithRemoteMessage[RoarPyRemoteSensorObsInfo, RoarPyRemoteSensorObsInfoRequest]):
    def __init__(
        self,
        start_info: RoarPyRemoteSensorObsInfo,
    ):
        RoarPySensor.__init__(self, name = start_info.name if start_info.name is not None else "RoarPyRemoteClientSensor", control_timestep = start_info.control_timestep)
        RoarPyObjectWithRemoteMessage.__init__(self)
        self._closed = False
        self._last_data = None
        self._new_data = None
        self._obs_spec = None
        self.new_request : RoarPyRemoteSensorObsInfoRequest = RoarPyRemoteSensorObsInfoRequest(
            close = False
        )
        self._depack_info(start_info)
    
    def _depack_info(self, data: RoarPyRemoteSensorObsInfo) -> bool:
        assert data.last_data_type in RoarPyRemoteSupportedSensorData._supported_data_types, f"Unsupported data type {data.last_data_type}"
        self._control_timestep = data.control_timestep
        
        new_data = data.get_last_obs()
        if new_data is not None:
            self._new_data = new_data
        
        new_obs_spec = data.get_obs_spec()
        if new_obs_spec is not None:
            self._obs_spec = new_obs_spec
        
        self._closed = data.is_closed
        return True
    
    def _pack_info(self) -> RoarPyRemoteSensorObsInfoRequest:
        return copy.copy(self.new_request)

    def get_gym_observation_spec(self) -> gym.Space:
        if self._obs_spec is None:
            raise RuntimeError("Observation spec is not available")
        return self._obs_spec
    
    async def receive_observation(self) -> _ObsT:
        while self._new_data is None:
            await asyncio.sleep(0.001)
        self._last_data = self._new_data
        return self._last_data
    
    def get_last_observation(self) -> Optional[_ObsT]:
        return self._last_data

    def convert_obs_to_gym_obs(self, obs: _ObsT):
        assert isinstance(obs, RoarPyRemoteSupportedSensorData)
        return obs.convert_obs_to_gym_obs()
    
    def close(self):
        self.new_request.close = True
    
    def is_closed(self) -> bool:
        return self._closed
    