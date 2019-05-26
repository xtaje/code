from datetime import date
from unittest import mock
import pytest
from allocation import events, handlers, exceptions, repository, unit_of_work


class FakeRepository(repository.AbstractRepository):

    def __init__(self, products):
        super().__init__()
        self._products = set(products)

    def _add(self, product):
        self._products.add(product)

    def _get(self, sku):
        return next((p for p in self._products if p.sku == sku), None)

    def _get_by_batchref(self, batchref):
        return next((
            p for p in self._products for b in p.batches
            if b.reference == batchref
        ), None)


class FakeUnitOfWork(unit_of_work.AbstractUnitOfWork):

    def __init__(self):
        self.init_repositories(FakeRepository([]))
        self.committed = False

    def _commit(self):
        self.committed = True

    def rollback(self):
        pass



class TestAddBatch:

    @staticmethod
    def test_for_new_product():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'sku1', 100, None, uow)
        assert uow.products.get('sku1') is not None
        assert uow.committed

    @staticmethod
    def test_for_existing_product():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'sku1', 100, None, uow)
        handlers.add_batch('b2', 'sku1', 99, None, uow)
        assert 'b2' in [b.reference for b in uow.products.get('sku1').batches]


class TestAllocate:

    @staticmethod
    def test_returns_allocation():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'sku1', 100, None, uow)
        result = handlers.allocate('o1', 'sku1', 10, uow)
        assert result == 'b1'

    @staticmethod
    def test_errors_for_invalid_sku():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'actualsku', 100, None, uow)

        with pytest.raises(exceptions.InvalidSku, match='Invalid sku nonexistentsku'):
            handlers.allocate('o1', 'nonexistentsku', 10, uow)


    @staticmethod
    def test_commits():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'sku1', 100, None, uow)
        handlers.allocate('o1', 'sku1', 10, uow)
        assert uow.committed

    @staticmethod
    def test_sends_email_on_out_of_stock_error():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'sku1', 9, None, uow)

        with mock.patch('allocation.email.send') as mock_send_mail:
            handlers.allocate('o1', 'sku1', 10, uow)
            assert mock_send_mail.call_args == mock.call(
                'stock@made.com',
                f'Out of stock for sku1',
            )


class TestChangeBatchQuantity:

    @staticmethod
    def test_changes_available_quantity():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'sku1', 100, None, uow)
        [batch] = uow.products.get(sku='sku1').batches
        assert batch.available_quantity == 100
        handlers.change_batch_quantity('b1', 50, uow)
        assert batch.available_quantity == 50


    @staticmethod
    def test_reallocates_if_necessary():
        uow = FakeUnitOfWork()
        handlers.add_batch('b1', 'sku1', 50, None, uow)
        handlers.add_batch('b2', 'sku1', 50, date.today(), uow)
        handlers.allocate('o1', 'sku1', 20, uow)
        handlers.allocate('o2', 'sku1', 20, uow)
        [batch1, batch2] = uow.products.get(sku='sku1').batches
        assert batch1.available_quantity == 10

        handlers.change_batch_quantity('b1', 25, uow)

        # o1 or o2 will be deallocated, so we'll have 25 - 20 * 1
        assert batch1.available_quantity == 5
        # and 20 will be reallocated to the next batch
        assert batch2.available_quantity == 30
