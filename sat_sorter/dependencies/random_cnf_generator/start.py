if __name__ == "__main__":
    print('Starting server.')

    import randomCNF
    from concurrent import futures
    import grpc
    from bee_rpc.client import serialize_to_buffer
    import api_pb2_grpc


    class RandomCnf(api_pb2_grpc.RandomServicer):
        def RandomCnf(self, request, context):
            for b in serialize_to_buffer(
                    message_iterator=randomCNF.ok()
            ): yield b


    print('Imported libs.')

    # create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    api_pb2_grpc.add_RandomServicer_to_server(
        RandomCnf(), server=server
    )

    print('Listening on port 8000.')
    server.add_insecure_port('[::]:8000')
    server.start()
    server.wait_for_termination()