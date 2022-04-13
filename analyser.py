#!/usr/bin/env python3
#
# Script leveraging mgz to graph the expenses of each player of a recorded Age
# of Empires 2 recording.

from json import loads
from logging import getLogger, StreamHandler, Formatter, DEBUG, INFO
from mgz import header, fast, enums, const
from pathlib import Path
from collections import namedtuple, defaultdict

# see mgz/enums.py , stone comes before gold, and thou can haveth fishes.
resource_names = ['wood','food','stone','gold']

class Expense:
    def __init__(self, wood, food, gold, stone):
        self.wood = wood
        self.food = food
        self.gold = gold
        self.stone = stone

    def __getitem__(self, resource):
        if type(resource) == int:
            resource = resource_names[resource]
        return getattr(self, resource)
    
    def __setitem__(self, resource, value):
        if type(resource) == int:
            resource = resource_names[resource]
        setattr(self, resource, value)

    def __str__(self):
        return 'Expense[{}]'.format(','.join(map(lambda x:str(self[x]), resource_names)))
        
def cost_to_expense(cost, amount = 1):
    '''
    Parses a JSON description of a cost from the AOE2 data specification into
    an expense object.
    '''
    if amount == None:
        amount = 1
    return Expense(
        amount * cost.get('wood',0),
        amount * cost.get('food',0),
        amount * cost.get('gold',0),
        amount * cost.get('stone',0),
    )


class RecordingParser:
    ignored_actions = [
        fast.Action.AI_ORDER,
        fast.Action.ATTACK_GROUND,
        fast.Action.BACK_TO_WORK,
        fast.Action.DELETE,
        fast.Action.DE_ATTACK_MOVE,
        fast.Action.DE_AUTOSCOUT,
        fast.Action.DE_UNKNOWN_196,
        fast.Action.DE_UNKNOWN_39,
        fast.Action.DE_UNKNOWN_41,
        fast.Action.FLARE,
        fast.Action.FORMATION,
        fast.Action.GAME,
        fast.Action.GATE,
        fast.Action.GATHER_POINT,
        fast.Action.GUARD,
        fast.Action.MOVE,
        fast.Action.MOVE,
        fast.Action.ORDER,
        fast.Action.PATROL,
        fast.Action.REPAIR,
        fast.Action.RESIGN,
        fast.Action.SPECIAL,
        fast.Action.STANCE,
        fast.Action.STOP,
        fast.Action.TOWN_BELL,
        fast.Action.UNGARRISON,
        fast.Action.WALL,
        fast.Action.WORK,
        fast.Action.MAKE,
    ]
    def __init__(self):
        self.setup_logging()
        self.aoe_data = self.load_aoe2_data()
        self.setup_market()

    def load_aoe2_data(self):
        '''
        Loads the ./halfon/data/units_buildings_techs.de.json file relatively to
        the currently executing Python module
        '''
        self.game_edition = 'de'
        # if you want, figure out the version and pick another file, but so far
        # let's not waste time
        assert self.game_edition in ('de','hd','wk')
        path = Path(__file__).parent.joinpath("halfon","data",f"units_buildings_techs.{self.game_edition}.json")
        self.logger.info(f'Loading the aoe2 data from {path}..')
        return loads(path.read_text())


    def setup_market(self):
        self.market_prices = {
                'wood': 100,
                'food': 100,
                'stone': 130,
        }
        self.market_fee_per_player = defaultdict(lambda: 0.3)
        

    def market_op(self, op, resource, amount, player_id):
        if amount > 1:
            # Shift-buy (=5 times) produces the same result as 5 clicks)
            self.logger.debug(f'Bulk ({amount}) market operation by player {player_id}')
            expense = Expense(0,0,0,0)
            for i in range(amount):
                tmp_exp = self.market_op(op, resource, 1, player_id)
                expense.wood += tmp_exp.wood
                expense.food += tmp_exp.food
                expense.gold += tmp_exp.gold
                expense.stone += tmp_exp.stone
            # sum the expenses
            return expense

        # https://ageofempires.fandom.com/wiki/Market_(Age_of_Empires_II)
        #
        # The Market prices for each commodity are universal for all players.
        # Commodities are sold or bought 100 at a time. Each commodity (food,
        # wood, stone) has a "fair" (and invisible) exchange price, but the
        # players cannot exchange their resources at the "fair" price because
        # there is a 30% commodity trading fee. At the beginning of each game,
        # the "fair" price for food and wood is 100, but because of the fee,
        # the actual prices the players see are 70 and 130. Stone, however,
        # begins at a "fair" price of 130, which leads to a starting rate of
        # 91/169. The game simulates supply and demand by adding or subtracting
        # 3 to the "fair" price each time 100 commodity resources are traded.
        # The minimum and maximum "fair" prices for any commodity are 20 and
        # 9,999, respectively. This means that to "bottom out" the price of a
        # commodity beginning at the starting price, the player needs to sell
        # 4,000 of food/wood or 5,500 stone (obtaining 1,708 or 2,926 gold).
        # Researching Guilds cuts the commodity trading fee in half (15%), but
        # can only be done in the Imperial Age and is not available to all
        # civilizations. If buying a resource would cost less than 25 gold, the
        # game will set the buy price to 25 gold. 

        # Convert resource identifiers into strings
        if type(resource) == int:
            resource = resource_names[resource]
        
        assert op in ('buy', 'sell')

        # default to 0.3 , whenever someone develops "Guilds", it's supposed to
        # become 0.15
        fee = self.market_fee_per_player[player_id]

        if op == 'buy':
            seen_price = self.market_prices[resource] * (1+fee)
            self.market_prices[resource] += 3
        elif op == 'sell':
            seen_price = self.market_prices[resource] * (1-fee)
            self.market_prices[resource] -= 3

        # Last line of the paragraph above
        if seen_price < 25:
            seen_price = 25

        # not sure about that one
        #seen_price = int(seen_price)

        if op == 'buy':
            expense = Expense(0,0,int(-1 * seen_price),0)
            expense[resource] = 100
        elif op == 'sell':
            expense = Expense(0,0,seen_price,0)
            expense[resource] = -100

        self.logger.debug(f'market op, player {player_id:2d} {op:4s} {resource:5s} at {seen_price} : {expense}')

        return expense

    def setup_logging(self):
        '''
        Sets up logging using the, uh, interesting Python3 API.
        '''
        logger = getLogger('analyser')
        #formatter = Formatter('%(asctime)s %(name)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        formatter = Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        stream_handler = StreamHandler()
        logger.setLevel(INFO)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        self.logger = logger

    def parse(self, filename):
        '''
        Loads all the events from the specified file into
        local variables.

        '''
        self.recording_filename = filename
        self.recording_path = Path(filename)
        file_size = self.recording_path.stat().st_size
        self.current_time = 0
        self.expenses = []

        assert self.recording_path.exists(), '''
        Provided file did not exist
        '''
        self.logger.info(f'Opening {self.recording_path}')
        with self.recording_path.open('rb') as descriptor:
            # Initiate the mgz parsing by checking out the
            # header
            self.logger.info('Parsing the header..')
            header_info = header.parse_stream(descriptor)
            self.logger.info('Parsing the "meta" thing..')
            fast.meta(descriptor)
            self.logger.info('Iterating on all operations..')
            while descriptor.tell() < file_size:
                op_type, op_data = fast.operation(descriptor)
                self.route_op(op_type, op_data)

        
    def route_op(self, op_type, op_data):
        if op_type == fast.Operation.ACTION:
            self.handle_action(op_data)
        elif op_type == fast.Operation.SYNC:
            ms_elapsed, _dropped = op_data
            self.current_time += ms_elapsed
            # Drop sync frames
            pass
        elif op_type == fast.Operation.VIEWLOCK:
            pass
        else:
            pass
            # CHAT, START, SAVE, SYNC, VIEWLOCK : we don't care about that.
            #raise Exception(NotImplemented)

    def handle_action(self, op_data):
        action_type, data = op_data
        if action_type == fast.Action.BUILD:
            _id = data['building_id']
            spec = self.aoe_data["units_buildings"][str(_id)]
            expense = cost_to_expense(spec["cost"])
        elif action_type == fast.Action.BUY:
            expense = self.market_op(
                    op = 'buy',
                    resource = data['resource_id'],
                    amount = data['amount'],
                    player_id = data['player_id'],
            )
            _id = 0
            spec = {
                    'name': f'buy {resource_names[data["resource_id"]]}'
            }
            # if data['resource_id'] > 1: # food & wood are OK, is stone number 2 ? buying gold makes no sense
            #     raise Exception()
            # answer: it was

        elif action_type == fast.Action.DE_QUEUE:
            _id = data['unit_id']
            spec = self.aoe_data["units_buildings"][str(_id)]
            expense = cost_to_expense(spec["cost"], amount = data.get('amount'))
        elif action_type == fast.Action.QUEUE:
            _id = data['unit_id']
            spec = self.aoe_data["units_buildings"][str(_id)]
            expense = cost_to_expense(spec["cost"], amount = data.get('amount'))
        elif action_type == fast.Action.CREATE:
            raise Exception()
        elif action_type == fast.Action.TRIBUTE:
            raise Exception()
        elif action_type == fast.Action.RESEARCH:
            _id = data['technology_id']
            spec = self.aoe_data["techs"][str(_id)]
            expense = cost_to_expense(spec["cost"])

            # Guilds cause the player marked prices to go from 30% to 15%
            if _id == 15:
                self.logger.info(f'Player {data["player_id"]} researched Guilds at {self.current_time}, fee set to 15%')
                self.market_fee_per_player[data['player_id']] = 0.15

        elif action_type == fast.Action.SELL:
            expense = self.market_op(
                    op = 'sell',
                    resource = data['resource_id'],
                    amount = data['amount'],
                    player_id = data['player_id'],
            )
            _id = 0
            spec = {
                    'name': f'sell {resource_names[data["resource_id"]]}'
            }
        elif action_type in RecordingParser.ignored_actions:
            # Some events are just ignored
            return
        else:
            self.logger.debug(f'Unhandled action {action_type} at {self.current_time} : {data}')
            # raise Exception()
            return

        self.record(action_type, data['player_id'], _id, expense, spec.get('name'))

    def record(self, action_type, player_id, obj_id, expense, internal_name):
        entry = [self.current_time, action_type, player_id, obj_id, expense.wood, expense.food, expense.gold, expense.stone, internal_name]
        self.logger.debug(f'Recording {entry}')

        # convert fast.Action.RESEARCH into 'RESEARCH' for export (and not into '101')
        entry[1] = entry[1].name
        self.expenses.append(entry)

    def export(self, dest = None):
        if dest == None:
            dest = self.recording_path.parent.joinpath(
                f'{self.recording_path.stem}.tsv'
            )
        self.logger.info(f'Writing into {dest}')
        # Nowadays CSV are TSV files, who would have guessed.
        dest.write_text('\n'.join([
            '\t'.join(['ts','type','player','id','wood','food','gold','stone','name']),
            '\n'.join(
                map(
                    lambda entry:'\t'.join(map(str, entry)),
                    self.expenses,
                )
            )
        ]))

        


if __name__ == '__main__':
    from argparse import ArgumentParser, RawDescriptionHelpFormatter
    parser = ArgumentParser(prog='aoe2record_expenses_to_csv.py', description='''
    A script extracting all the resource-related operations of an AOE2:DE game
    recording (.aoe2record) into a CSV file for further analysis of the
    resources spent.

    It also happens to be a reusable implementation of a generic game analyser
    leveraging the mgz library, inspired from the implementation of
    AoE_Rec_Opening_Analysis.

    It relies on the halfon JSON data file being placed in
    ./halfon/data/units_buildings_techs.de.json , which is what happens when
    git cloning that repo.

    * https://github.com/happyleavesaoc/aoc-mgz
    * https://github.com/SiegeEngineers/halfon/tree/master/data/units_buildings_techs.de.json
    * https://github.com/dj0wns/AoE_Rec_Opening_Analysis/blob/main/aoe_replay_stats.py
    ''',
    formatter_class = RawDescriptionHelpFormatter,
    )
    parser.add_argument('file', help='''
    The multiplayer recording you're willing to analyse
    ''')
    parser.add_argument('-o','--output',help='''
    The path prefix for output files. If empty, removes the
    recording extension and places the output files in the
    same folder.
    ''')
    parser.add_argument('-d','--debug', action='store_true', help='''
    Sets the logging level to DEBUG, shows all the events being parsed and
    their actions on the maintained state (notably, the market prices.
    ''')
    args = parser.parse_args()

    import pdb, sys, traceback
    def info(type, value, tb):
        traceback.print_exception(type, value, tb)
        pdb.pm()
    sys.excepthook = info

    rp = RecordingParser()
    if args.debug:
        rp.logger.setLevel(DEBUG)
    rp.parse(args.file)
    rp.export(args.output)
