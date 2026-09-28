class DesignError(Exception):
    exit_code = 2


class CapabilityError(DesignError):
    exit_code = 3


class ExecutionError(DesignError):
    exit_code = 4
