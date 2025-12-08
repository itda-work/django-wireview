from django.shortcuts import get_object_or_404, render

from .models import Product


def index(request):
    """List all products."""
    products = Product.objects.all()
    return render(request, "rating/index.html", {"products": products})


def product_detail(request, product_id):
    """Show product with rating interface."""
    product = get_object_or_404(Product, id=product_id)
    return render(request, "rating/detail.html", {"product": product})
