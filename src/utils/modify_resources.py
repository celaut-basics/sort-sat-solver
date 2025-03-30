from node_controller.gateway.protos import celaut_pb2
from node_controller.gateway.communication import modify_resources

from src.envs import ENVS, DEV_MODE

if DEV_MODE:
    MODIFY_SYSTEM_RESOURCES_LAMBDA = lambda d: (celaut_pb2.Sysresources(
                    mem_limit=d['max']
                ), 0)
else:
    MODIFY_SYSTEM_RESOURCES_LAMBDA = lambda d: modify_resources(i=d, node_url=ENVS['GATEWAY_MAIN_DIR'])
