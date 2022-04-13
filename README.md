# aoe-analysis

# Purpose
This program leverages the `mgz` library to extract resource expenditure data from AOE2DE games.

So far, it just converts .aoe2record files into .tsv files, keeping only events related to resource management.

It requires the 'halfon' project to be cloned in the same folder as itself. I'm no git expert and couldn't find how to include these fancy inter-git symlinks things.


```
usage: aoe2record_expenses_to_csv.py [-h] [-o OUTPUT] [-d] file

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


positional arguments:
  file                  The multiplayer recording you're willing to analyse

optional arguments:
  -h, --help            show this help message and exit
  -o OUTPUT, --output OUTPUT
                        The path prefix for output files. If empty, removes the recording extension and places the output files in the same
                        folder.
  -d, --debug           Sets the logging level to DEBUG, shows all the events being parsed and their actions on the maintained state (notably,
                        the market prices.
```                        


# limitations

As you might know, the recordings don't contain any info on resources being collected by players. As such, we can only deduce expenses by using the prices of some costly actions (building units & buildings).

No per-civilisation bonuses have been implemented. Feel free to do so :D (Actually, a proper way to get metrics is to use the Voobly DLL exposing a websocket API to pull live metrics data from replayed games with a live AOE2DE engine running. See https://github.com/Macuyiko/aoe2predict/ and https://blog.macuyiko.com/post/2018/predicting-voobly-age-of-empires-2-matches.html for such an impressive feat. We've basically chosing the "Approach 1" from this blog post.

A small market state is kept to deduce the prices of resources, using https://ageofempires.fandom.com/wiki/Market_(Age_of_Empires_II) as a reference. Developing "Guilds" gives a player 15% fee instead of 30% fee. A problem is that one player can click several times on the button to develop "Guilds", and I could not find the item describing that technology research being canceled. Wich means the first click is counted as an instant development, regardless of canceling that technology or doing it later on.

# Sample usage


Saves are usually in `C:\Users\<username>\Games\Age of Empires 2 DE\<user_id>\savegame`, there's a button in the AOE2DE UI to access this anyways. Copy `$env:USERPROFILE\Games\Age of Empires 2 DE\` in a powershell instance and use tab-completion to save time. Or not. I'm a sign, not a cop.

```
PS D:\git\aoe-analysis> py -3 .\analyser.py '.\MP Replay v101.101.59165.0 @2022.04.10 213846 (6).aoe2record'
2022-04-13 12:52:22 INFO Loading the aoe2 data from D:\git\aoe-analysis\halfon\data\units_buildings_techs.de.json..
2022-04-13 12:52:22 INFO Opening MP Replay v101.101.59165.0 @2022.04.10 213846 (6).aoe2record
2022-04-13 12:52:22 INFO Parsing the header..
2022-04-13 12:52:41 INFO Parsing the "meta" thing..
2022-04-13 12:52:41 INFO Iterating on all operations..
2022-04-13 12:52:42 CRITICAL Unhandled action Action.ATTACK_GROUND at 2094450 : {'object_ids': [56344], 'x': 35.95833206176758, 'y': 127.0}
2022-04-13 12:52:42 INFO Player 1 researched Guilds at 2670249, fee set to 15%
2022-04-13 12:52:42 INFO Player 6 researched Guilds at 2732209, fee set to 15%
2022-04-13 12:52:42 INFO Player 4 researched Guilds at 2764689, fee set to 15%
2022-04-13 12:52:42 INFO Player 6 researched Guilds at 3065409, fee set to 15%
2022-04-13 12:52:42 INFO Player 6 researched Guilds at 3503169, fee set to 15%
2022-04-13 12:52:42 INFO Writing into MP Replay v101.101.59165.0 @2022.04.10 213846 (6).tsv
PS D:\git\aoe-analysis> Get-Content '.\MP Replay v101.101.59165.0 @2022.04.10 213846 (6).tsv' | Select-Object -first 20
ts      type    player  id      wood    food    gold    stone   name
1352    RESEARCH        4       22      0       0       50      0       Loom
1560    DE_QUEUE        8       83      0       50      0       0       VMBAS
1768    DE_QUEUE        4       83      0       50      0       0       VMBAS
1768    DE_QUEUE        7       83      0       50      0       0       VMBAS
1976    DE_QUEUE        4       83      0       50      0       0       VMBAS
1976    DE_QUEUE        8       83      0       50      0       0       VMBAS
2184    DE_QUEUE        7       83      0       50      0       0       VMBAS
2184    DE_QUEUE        7       83      0       50      0       0       VMBAS
2392    DE_QUEUE        6       83      0       250     0       0       VMBAS
2600    DE_QUEUE        7       83      0       50      0       0       VMBAS
2808    DE_QUEUE        7       83      0       50      0       0       VMBAS
2808    DE_QUEUE        7       83      0       50      0       0       VMBAS
3224    BUILD   8       70      25      0       0       0       HOUS
3605    BUILD   4       70      25      0       0       0       HOUS
5054    BUILD   1       70      25      0       0       0       HOUS
5712    BUILD   6       70      25      0       0       0       HOUS
5712    BUILD   8       70      25      0       0       0       HOUS
5920    BUILD   1       70      25      0       0       0       HOUS
7560    BUILD   6       70      25      0       0       0       HOUS
```

Graphing these will be done later on. So far, we have usable data :)
