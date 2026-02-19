"""Base abstract class for memory implementations."""

from abc import ABC, abstractmethod


class MemoryBase(ABC):
    """Base class for all memory implementations."""

    @abstractmethod
    def get_all(self):
        """List all memories.

        Returns:
            list: List of all memories.
        """
        pass

    @abstractmethod
    def delete(self, memory_id):
        """Delete a memory by ID.

        Args:
            memory_id (str): ID of the memory to delete.
        """
        pass

    @abstractmethod
    def history(self, memory_id):
        """Get the history of changes for a memory by ID.

        Args:
            memory_id (str): ID of the memory to get history for.

        Returns:
            list: List of changes for the memory.
        """
        pass
