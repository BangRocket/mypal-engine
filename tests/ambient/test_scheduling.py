from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import scheduling
from mypalclara.db.models import AmbientUserConfig, Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/u.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_opted_in_users(tmp_path):
    sf = _factory(tmp_path)
    db = sf()
    db.add(AmbientUserConfig(user_id="discord-1", reflection_opt_in="true"))
    db.add(AmbientUserConfig(user_id="discord-2", reflection_opt_in="false"))
    db.commit()
    db.close()
    assert scheduling.get_opted_in_users(session_factory=sf) == ["discord-1"]


def test_register_adds_one_cron_task():
    added = []

    class _Sched:
        def add_task(self, task):
            added.append(task)

    async def runner():
        return None

    scheduling.register_ambient_task(_Sched(), runner=runner, cron="0 11 * * *")
    assert len(added) == 1
    assert added[0].name == "ambient_tick"
    assert added[0].cron == "0 11 * * *"
    assert added[0].handler is runner
