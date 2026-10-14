from node import Node


def __main__():
    # Create a OLT node
    OLT = Node("root", "OLT", ports=2)

    # Create PON nodes
    PON1 = Node("child1", "PON", parent=OLT, parent_port=0, ports=2)
    PON2 = Node("child2", "PON", parent=OLT, parent_port=1, ports=2)

    # Create ONT nodes
    ONT1 = Node("grandchild1", "ONT", parent=PON1, parent_port=0, ports=0)
    ONT2 = Node("grandchild2", "ONT", parent=PON1, parent_port=1, ports=0)
    ONT3 = Node("grandchild3", "ONT", parent=PON2, parent_port=0, ports=0)
    ONT4 = Node("grandchild4", "ONT", parent=PON2, parent_port=1, ports=0)

    # Simulate faults
    PON2.set_fault("down")
    ONT3.set_fault("hung")

    # Print the status of each node
    print(f"{OLT.name} status: {OLT.status()}")
    print(f"{PON1.name} status: {PON1.status()}")
    print(f"{PON2.name} status: {PON2.status()}")
    print(f"{ONT1.name} status: {ONT1.status()}")
    print(f"{ONT2.name} status: {ONT2.status()}")
    print(f"{ONT3.name} status: {ONT3.status()}")
    print(f"{ONT4.name} status: {ONT4.status()}")



if __name__ == "__main__":
    __main__()