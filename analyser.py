#!/usr/bin/env python3
#
# Script leveraging mgz to graph the expenses of each player of a recorded Age
# of Empires 2 recording.

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from bokeh.embed import file_html
from bokeh.resources  import settings
settings.resources = 'inline'
from bokeh.io import curdoc
from bokeh.layouts import gridplot
from bokeh.models import DatetimeTickFormatter, FuncTickFormatter, FixedTicker, Span, ColumnDataSource
from bokeh.plotting import figure, show, output_file
from bokeh.resources import INLINE
from bokeh.palettes import Spectral
from collections import namedtuple, defaultdict
from construct import (Struct, CString, Const, Int32ul, Embedded, Float32l, If, Computed, Peek)
from datetime import datetime
from json import loads, dumps
from logging import getLogger, StreamHandler, Formatter, DEBUG, INFO
from mgz import header, fast, enums, const
from mgz.header.de import de
from mgz.util import MgzPrefixed, ZlibCompressed, Version, VersionAdapter, get_version
from pandas import DataFrame
from pathlib import Path
from webbrowser import open as webbrowser_open

# see mgz/enums.py , stone comes before gold, and thou can haveth fishes.
resource_names = ['wood','food','stone','gold']
resource_colors = {
        'wood':'brown',
        'food':'red',
        'stone':'grey',
        'gold':'gold',
}
age_colors = {
        'Feudal Age': 'red',
        'Middle Age': 'blue', # name != localised_name in the halfon data file
        'Castle Age': 'blue',
        'Imperial Age': 'green',
}

AOE_PLAYER_COLORS = [
    "#89f7e6", # Sky Blue (Gaia)
    "#3783ff", # Blue
    "#fa0101", # Red
    "#f9f709", # Yellow
    "#936608", # Brown
    "#fc9a02", # Orange
    "#01fc01", # Lime
    "#fb01fa", # Magenta
    "#05faf9", # Cyan
]

TSV_COLUMN_NAMES = [
        'ts','type','player','id',
        'wood','food','gold','stone',
        'name',
        'wood_market','food_market','stone_market',
]

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


def ts_to_datetime(ts):
    return datetime.fromtimestamp(ts/1000)

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
    def __init__(self, args):
        self.args = args
        self.setup_logging()
        self.aoe_data = self.load_aoe2_data()
        self.setup_market()
        self.players = dict()
        self.teams = defaultdict(list)



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
        if self.market_prices[resource] < 25:
            self.market_prices[resource] = 25

        # As it turns out, since we're not approximating live resource counts,
        # counting the negative expenses serves no purpose and breaks the
        # assumption that expenses are only positive, etc. This means I'm
        # disabling the negative resource amounts below ( = no resource income
        # when buying, = no gold income when selling)
        if op == 'buy':
            expense = Expense(0,0,seen_price,0)
            # expense[resource] = -100
            # We're counting expenses, so paying 50 food for a villager is positive.
            # Which means, receiving 100 food is negative.
        elif op == 'sell':
            #expense = Expense(0,0,-1 * seen_price,0)
            expense = Expense(0,0,0,0)
            expense[resource] = 100
            # Same here, it's an expenditure of 100 food, positive.

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
        # debug mode
        if self.args.debug:
            logger.setLevel(DEBUG)

        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        self.logger = logger

    def parse(self, filename, accept_cache = False):
        '''
        Loads all the events from the specified file into
        local variables.

        '''
        self.recording_filename = filename
        self.recording_path = Path(filename)
        file_size = self.recording_path.stat().st_size
        self.current_time = 0
        self.expenses = []
        self.accept_cache = accept_cache

        assert self.recording_path.exists(), '''
        Provided file did not exist
        '''


        # Regardless of the cached TSV, always load player names straight from
        # the file
        self.get_player_names_from_recording()


        cached_tsv_path = self.recording_path.parent.joinpath(
            f'{self.recording_path.stem}.tsv'
        )
        if self.accept_cache and cached_tsv_path.exists():
            self.logger.info(f'Loading the cached TSV data from {cached_tsv_path}')
            # Load the TSV, 
            data = list(
                    map(
                        lambda x:x.strip('\n').split('\t'),
                        cached_tsv_path.read_text().splitlines(),
                )
            )
            # Convert to float except fields 
            tsv_header = data[0]
            nofloat_columns = [tsv_header.index(x) for x in ['type','name']]
            for entry in data[1:]:
                self.expenses.append([float(entry[i]) if i not in nofloat_columns else entry[i] for i in range(len(entry))])

            self.logger.info(f'Loaded {len(self.expenses)} expenses records from {cached_tsv_path}')
            return


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

        # If a better name exists, extract it out. Yeah that's localised in
        # english in the halfon data file, but heh just switch files if you
        # need that, but since how everyone plays in english these days. Well. 
        if spec.get('localised_name') not in ['', None]:
            spec['name'] = spec['localised_name']

        self.record(action_type, data['player_id'], _id, expense, spec.get('name'))

    def record(self, action_type, player_id, obj_id, expense, internal_name):
        entry = [
                self.current_time,
                action_type,
                player_id,
                obj_id,
                expense.wood,
                expense.food,
                expense.gold,
                expense.stone,
                internal_name,
                self.market_prices['wood'],
                self.market_prices['food'],
                self.market_prices['stone'],
        ]
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
            '\t'.join(TSV_COLUMN_NAMES),
            '\n'.join(
                map(
                    lambda entry:'\t'.join(map(str, entry)),
                    self.expenses,
                )
            )
        ]))


    def get_ages_bars(self, player_id):
        for age_name, timestamp in self.players[player_id]['ages'].items():
            # Add a vertical line at the right millisecond
            yield Span(
                location = ts_to_datetime(timestamp),
                dimension = 'height',
                line_width = 3,
                # line_dash = 'dashed',
                line_color = age_colors[age_name],
            )

    def set_ages_bar(self, plot, player_id):
        for bar in self.get_ages_bars(player_id):
            plot.add_layout(bar)

    def ensure_dataframe(self):
        '''
        Convert the self.expenses list into a dataframe, storing it in self.df,
        but only if it wasn't there.
        '''
        if hasattr(self, 'df'):
            return

        df = DataFrame.from_records(
                self.expenses,
                columns = TSV_COLUMN_NAMES,
        )

        # Calculate the unified spending evaluation
        df['unified']  = df['food']  * (df['food_market']/100) \
                       + df['wood']  * (df['wood_market']/100) \
                       + df['gold'] \
                       + df['stone'] * (df['stone_market']/100)


        self.logger.info(f'Built a pandas DataFrame:\n{df}')
        self.df = df

    def extract_age_times(self):
        self.ensure_dataframe()
        df = self.df
        # First of all, extract the age changes for each player
        _tmp = df[(df['type'] == 'RESEARCH') & (df['name'].str.endswith('Age')) ][['ts','player','name']]
        for player_id in sorted(self.players):
            _ages = _tmp[_tmp["player"] == player_id][["ts","name"]].set_index("name").to_dict()["ts"]
            # {'Middle Age': 671208.0, 'Feudal Age': 1415278.0}
            self.logger.debug(f'Extracted {self.players[player_id]["name"]} ages: {_ages}')
            self.players[player_id]['ages'] = _ages


    def get_plot_expenses(self, player_id, plot_props = {}):
        '''
        Returns a plot of cumulative expenses for a specific player
        '''
        player_info = self.players[player_id]
        player_name = player_info['name']
        self.logger.info(f'Handling player {player_id}: {player_name}')

        pdf = self.get_player_ops(player_id)

        x = list(map(ts_to_datetime, pdf['ts']))

        # create a new plot with a title and axis labels
        p = figure(title=f"{player_name} expenses over time", x_axis_label='time', y_axis_label='amount spent', **plot_props)
        p.xaxis[0].formatter = DatetimeTickFormatter()

        # add a line renderer with legend and line thickness to the plot
        for resource in resource_names:
            y = list(pdf[resource])
            # cumulative, instead of flat
            for i in range(1,len(y)):
                y[i] += y[i-1]
            p.line(x, y, legend_label = resource, line_width=2, color=resource_colors[resource])


        # Add the player ages
        self.set_ages_bar(p, player_id)
        p.legend.location = 'top_left'

        # Return the plot
        return p


    def get_plot_units(self, player_id, plot_props = {}):
        p = self.get_plot_objects('DE_QUEUE','units', player_id, plot_props)
        return p

    def get_plot_buildings(self, player_id, plot_props = {}):
        p = self.get_plot_objects('BUILD','buildings', player_id, plot_props)
        return p

    def get_player_ops(self, player_id, CATEGORY = None):
        self.ensure_dataframe()
        df = self.df
        pdf = df[df['player'] == player_id]
        if CATEGORY is not None:
            pdf = pdf[pdf['type'] == CATEGORY]
        return pdf

    def get_plot_objects(self, CATEGORY, title, player_id, plot_props = {}):
        df = self.df
        player_info = self.players[player_id]
        player_name = player_info['name']

        self.logger.info(f'{title} for player {player_id}: {player_name}')
        pdf = self.get_player_ops(player_id, CATEGORY)

        x = list(map(ts_to_datetime, pdf['ts']))

        # create a new plot with a title and axis labels
        p = figure(title=f"{player_name} {title} over time", x_axis_label='time', sizing_mode='stretch_both')
        p.xaxis[0].formatter = DatetimeTickFormatter()

        # Add a circle for each unit produced
        ids = list(map(int,pdf['id']))
        # remap the ids contiguously, using the user creation order
        all_seen_ids = []
        for i in ids:
            if i not in all_seen_ids:
                all_seen_ids.append(i)
        y = [all_seen_ids.index(i) for i in ids]

        # Build a dictionnary of names per id, 
        names_dict = pdf[['id','name']].drop_duplicates().set_index('id').to_dict('index')
        _tmp = dict()
        for k in names_dict:
            # and take care of changing from the AOE ids into the per-player ids
            _tmp[all_seen_ids.index(k)] = f'{names_dict[k]["name"]}'
        names_dict = _tmp

        code = f'''
        var labels = {dumps(names_dict)};
        return labels[tick] || tick;
        '''
        p.yaxis.formatter = FuncTickFormatter(code = code)
        # show all lines
        p.yaxis.ticker = FixedTicker(ticks = sorted(set(y)))

        p.circle(x, y, legend_label = title, color="gold")

        self.set_ages_bar(p, player_id)
        p.legend.location = 'top_left'
        return p

    def get_unified_market_review(self, plot_props = {}):
        xdf = self.df

        colors = AOE_PLAYER_COLORS

        # create a new plot with a title and axis labels
        p = figure(title=f"Merged expenses over time", x_axis_label='time', y_axis_label='amount spent', **plot_props)
        p.xaxis[0].formatter = DatetimeTickFormatter()
        for player_id in sorted(self.players):
            pdf = xdf[xdf['player'] == player_id]
            x = list(map(ts_to_datetime, pdf['ts']))
            y = list(pdf['unified'])
            # cumulative, instead of flat
            for i in range(1,len(y)):
                y[i] += y[i-1]
            player_name = self.players[player_id]['name']
            p.line(
                    x,
                    y,
                    legend_label = f'{player_name}',
                    line_width=2,
                    color = AOE_PLAYER_COLORS[self.players[player_id]['color_id']],
            )

        # # Add the player ages
        # self.set_ages_bar(p, player_id)

        p.legend.location = 'top_left'
        return p

    def get_unified_market_review_team(self, plot_props = {}):
        # First of all, let's have a simple model where we don't care about the
        # market prices over time and just use 100/100/130.

        xdf = self.df

        p = figure(title=f"Team expenses over time", x_axis_label='time', y_axis_label='amount spent', **plot_props)
        p.xaxis[0].formatter = DatetimeTickFormatter()

        arrays = []
        for team_id, team_members in self.teams.items():
            if team_id == 1:
                # Unaligned members
                for team_member in team_members:
                    player_id = team_member.get('player_number')
                    pdf = xdf[xdf['player'] == player_id]
                    x = list(map(ts_to_datetime, pdf['ts']))
                    y = list(pdf['unified'])
                    # cumulative, instead of flat
                    for i in range(1,len(y)):
                        y[i] += y[i-1]
                    player_name = self.players[player_id]['name']
                    arrays.append([x,y,f'Unaligned {player_name}'])
            else:
                pnames = ','.join(map(lambda x:x.get('name') ,team_members))
                team_name = f'Team {team_id} ({pnames})'
                player_ids = set(map(lambda x:x.get('player_number'), team_members))
                pdf = xdf[xdf['player'].isin(player_ids)]
                x = list(map(ts_to_datetime, pdf['ts']))
                y = list(pdf['unified'])
                # cumulative, instead of flat
                for i in range(1,len(y)):
                    y[i] += y[i-1]
                arrays.append([x,y,team_name])


        # For each of the teams, add a line
        if len(arrays) < 3:
            colors = Spectral[3]
        else:
            colors = Spectral[len(arrays)]
        i = 0
        for x,y,name in arrays:
            p.line(x, y, legend_label = f'{name}', line_width=2, color=colors[i])
            i += 1

        p.legend.location = 'top_left'
        return p

 
    def get_market_prices_over_time(self, plot_props = {}):
        # create a new plot with a title and axis labels
        p = figure(title=f"Market prices", x_axis_label='time', y_axis_label='gold price', **plot_props)
        p.xaxis[0].formatter = DatetimeTickFormatter()

        data = ColumnDataSource(self.df)
        for resource in ['wood','food','stone']:
            p.line(
                source = data,
                x = 'ts',
                y = f'{resource}_market',
                legend_label = f'{resource} price over time',
                color = resource_colors[resource],
                line_width=4,
            )

        # # Add the player ages
        # self.set_ages_bar(p, player_id)

        p.legend.location = 'top_left'
        return p
 

    def plot(self, dest = None):
        if dest == None:
           dest = self.recording_path.parent.joinpath(
               f'{self.recording_path.stem}.html'
           )
        self.logger.info(f'Writing into {dest}')


        # Register a global bokeh destination file
        output_file(dest)
        # https://docs.bokeh.org/en/latest/docs/user_guide/styling.html
        curdoc().theme = "dark_minimal"

        # Some data preparation
        self.ensure_dataframe()
        df = self.df
        self.extract_age_times()

        # generic plot sizing properties
        plot_props = {
                'sizing_mode': 'stretch_both',
                'min_width': 400,
                'min_height': 500,
        }

        # Prepare a grid of small figures
        figs = [[]]

        # First row is expenses over time
        for player_id in sorted(self.players):
            p = self.get_plot_expenses(player_id, plot_props)
            figs[0].append(p)

        # Second row is units over time
        figs.append([])
        for player_id in sorted(self.players):
            p = self.get_plot_units(player_id, plot_props)
            figs[-1].append(p)

        # Third row is bulidings
        figs.append([])
        for player_id in sorted(self.players):
            p = self.get_plot_buildings(player_id, plot_props)
            figs[-1].append(p)

        # Fourth row is a fancy evaluation of spendings per market cost over time
        figs.append([
            self.get_unified_market_review(plot_props),
            self.get_unified_market_review_team(plot_props),
        ])


        # Fifth row is about market prices
        figs.append([
            self.get_market_prices_over_time(plot_props),
        ])


        # Assemble all the small figures into a single grid plot
        p = gridplot(figs)
        show(p)

        # # Using file_html instead of p.show() allows us to specify
        # # resources=INLINE, which allows offline rendering, useful for whenever
        # # you're on the highway burning petrol on your way to the countryside
        # dest = Path('a.html')
        # self.logger.debug('Generating HTML..')
        # html_text = file_html(p, INLINE, f'{self.recording_filename} statistics', theme="dark_minimal")
        # self.logger.debug(f'Writing into {dest}')
        # dest.write_text(html_text)
        # webbrowser_open(str(dest.absolute()))

    def get_player_names_from_recording(self):
        '''
        Extracts the player names from the compressed header of a recording.

        Also, sets the teams.
        '''
        path = Path(self.recording_filename)
        assert path.exists()
    
        # Q: Is there a fast way to just load the player names from a recording with mgz withotu loading its entirety with header.parse_stream(descriptor) ?
        # A: happyleaves — Yesterday at 10:35 PM : @global_uuid_database yes
        # that basically only parses the version numbers and DE header block, which has player names etc
    
        compressed_header = Struct(
            "game_version"/CString(encoding='latin1'),
            "save_version"/VersionAdapter(Float32l),
            "version"/Computed(lambda ctx: get_version(ctx.game_version, ctx.save_version, None)),
            "de"/If(lambda ctx: ctx.version == Version.DE, de),
        )
        
        subheader = Struct(
            "check"/Peek(Int32ul),
            "chapter_address"/If(lambda ctx: ctx.check < 100000000, Int32ul),
            Embedded(MgzPrefixed(lambda ctx: ctx._.header_length - 4 - (4 if ctx.check < 100000000 else 0), ZlibCompressed(compressed_header)))
        )
        
        """Header is compressed"""
        header = Struct(
            "header_length"/Int32ul,
            Embedded(subheader),
            "log_version"/If(lambda ctx: ctx.save_version >= 11.76, Peek(Int32ul)),
            "version"/Computed(lambda ctx: get_version(ctx.game_version, ctx.save_version, ctx.log_version))
        )
        
        with path.open('rb') as h:
            for player in header.parse_stream(h).de.players:
                # if not player.name.value:
                #     # Skip players with no name (?)
                #     self.logger.debug(f'Skipping player {player.player_number} as it has no name ? {player}')
                #     continue
                fields = [
                        'civ_id',
                        'color_id',
                        'player_number',
                        'type',
                        'selected_team_id',
                        'resolved_team_id',
                ]
                _p = dict(zip(
                    fields, 
                    map(lambda x:getattr(player, x), fields),
                ))
                if player.name.value:
                    if self.args.privacy:
                        _p['name'] = f'P{player.player_number}'
                    else:
                        _p['name'] = player.name.value.decode('utf-8')

                elif player.ai_name.value:
                    _p['name'] = " ".join([
                        player.ai_type.value.decode('utf-8'),
                        player.ai_name.value.decode('utf-8'),
                    ])
                elif player.type == 'closed':
                    continue
                else:
                    self.logger.warning(f'''Unable to handle player info for {player}, it seems it's neither a human, an AI, or closed.''')
                    raise Exception()

                self.players[player.player_number] = _p
                self.teams[player.resolved_team_id].append(_p)

    def show_usernames(self, filename):
        self.recording_filename = filename
        self.recording_path = Path(filename)
        self.get_player_names_from_recording()
        usernames = sorted([p.get('name') for i,p in self.players.items()])
        print(f'{filename}: {usernames}')

if __name__ == '__main__':
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
    parser.add_argument('-a','--action', default='analyse', help='''
    convert: just generates a .tsv depicting resource usage
    analyse: generates (if needed) a .tsv, then graph it out
    ''')
    parser.add_argument('-d','--debug', action='store_true', help='''
    Sets the logging level to DEBUG, shows all the events being parsed and
    their actions on the maintained state (notably, the market prices.
    ''')
    parser.add_argument('-p','--privacy', action='store_true', help='''
    Hides the human player names so that you don't cause your friends bad opsec
    practices to go bananas.
    ''')
    args = parser.parse_args()

    import pdb, sys, traceback
    def info(type, value, tb):
        traceback.print_exception(type, value, tb)
        pdb.pm()
    sys.excepthook = info

    if args.action == 'convert':
        rp = RecordingParser(args)
        rp.parse(args.file)
        rp.export(args.output)
    elif args.action == 'analyse':
        rp = RecordingParser(args)
        rp.parse(args.file, accept_cache = True)
        rp.export(args.output)
        rp.plot(args.output)
    elif args.action == 'usernames':
        rp = RecordingParser(args)
        rp.show_usernames(args.file)
    else:
        assert False, f'unknown action {args.action}'
        raise Exception()

