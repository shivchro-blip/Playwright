# dictionary.py

class Dictionary:
    _instance = None

    def __new__(cls, *args, **kwargs):  #Single Dictionary
        if cls._instance is None:
            cls._instance = super(Dictionary, cls).__new__(cls, *args, **kwargs)
            cls._instance._global_variables = {}
        return cls._instance

    def save(self, key, value):
        self._global_variables[key] = value

    def load(self, key):
        return self._global_variables.get(key) #return value of key else None

    def loadAll(self):
        return self._global_variables