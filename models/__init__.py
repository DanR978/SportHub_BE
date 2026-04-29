# These imports look unused but they aren't — importing the model module
# registers the class on `Base.metadata` so `Base.metadata.create_all(...)`
# in tests + dev startup picks up every table. pyflakes/F401 is silenced
# rather than restructured.

from models.db_user import DBUser  # noqa: F401
from models.db_event import DBEvent  # noqa: F401
from models.db_event_participant import DBEventParticipant  # noqa: F401
from models.db_event_ban import DBEventBan  # noqa: F401
from models.db_archived_event import DBArchivedEvent  # noqa: F401
from models.db_host_rating import DBHostRating  # noqa: F401
from models.db_bookmark import DBBookmark  # noqa: F401
from models.db_block import DBBlock  # noqa: F401
from models.db_report import DBReport  # noqa: F401
from models.db_device_token import DBDeviceToken  # noqa: F401
from models.db_post import DBPost  # noqa: F401
from models.db_post_reaction import DBPostReaction  # noqa: F401
from models.db_comment import DBComment  # noqa: F401
from models.db_comment_reaction import DBCommentLike  # noqa: F401
from models.db_conversation import DBConversation, DBConversationMember  # noqa: F401
from models.db_conversation_ban import DBConversationBan  # noqa: F401
from models.db_message import DBMessage  # noqa: F401
from models.db_friendship import DBFriendship  # noqa: F401
