from django.db import models


class Cars(models.Model):
    make = models.CharField(max_length=5)
    model = models.CharField(max_length=20)
    year = models.CharField(max_length=4)

    def get_descrption(self, request):
        return f"{self.year} {self.make} {self.model}"


