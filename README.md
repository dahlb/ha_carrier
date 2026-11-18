# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                 |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| custom\_components/ha\_carrier/\_\_init\_\_.py                       |      130 |       12 |       24 |        6 |     88% |176, 178-180, 219-220, 253, 294, 297-300, 304, 311, 327-\>332 |
| custom\_components/ha\_carrier/binary\_sensor.py                     |       57 |        2 |        6 |        2 |     94% |37-\>43, 77, 93 |
| custom\_components/ha\_carrier/carrier\_data\_update\_coordinator.py |      261 |       25 |       84 |       15 |     87% |233, 241-\>exit, 258, 289, 307, 318, 330-\>exit, 390, 460, 465-466, 510, 547-553, 572-\>578, 582-589, 623-624, 630, 665-668, 680-\>679, 682, 700, 720-721 |
| custom\_components/ha\_carrier/carrier\_entity.py                    |       83 |        9 |       26 |        9 |     83% |77-84, 124, 139-\>138, 141-142, 156-\>155, 158-159, 199, 226-\>228 |
| custom\_components/ha\_carrier/climate.py                            |      300 |       31 |      106 |       23 |     85% |95-\>97, 97-\>99, 99-\>102, 223, 247, 272, 282, 287, 313, 387-\>exit, 426-435, 460, 500-\>503, 551, 558-559, 561-562, 565, 630, 636, 649-650, 652-653, 657, 759 |
| custom\_components/ha\_carrier/config\_flow.py                       |      126 |        5 |       38 |        9 |     91% |63-\>70, 71-72, 157-\>174, 216, 224, 268-\>297, 283-\>288, 289 |
| custom\_components/ha\_carrier/const.py                              |       40 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/diagnostics.py                        |       32 |        0 |        8 |        2 |     95% |63-\>46, 80-\>87 |
| custom\_components/ha\_carrier/entry\_level\_climate.py              |      126 |       17 |       36 |       17 |     79% |115, 146, 154, 157, 159, 161, 169, 174, 182, 190, 200, 205-\>210, 207, 211, 213, 239, 250-\>252, 256, 260 |
| custom\_components/ha\_carrier/exceptions.py                         |        2 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/migrate.py                            |      234 |       41 |      112 |       26 |     78% |96, 112, 127, 177, 181-183, 187, 221, 223-231, 242-247, 251, 270-279, 284-293, 298, 344-\>351, 415-\>418, 418-\>420, 420-\>422, 422-\>425, 437-\>452, 463-\>452, 496, 505-514, 519-528, 593-\>601, 596-597, 602, 670-\>677, 673-674 |
| custom\_components/ha\_carrier/resiliency.py                         |      104 |        5 |       30 |        4 |     93% |262, 268, 275, 281, 291 |
| custom\_components/ha\_carrier/select.py                             |       52 |        6 |        8 |        4 |     83% |70, 86, 117, 133, 165-166 |
| custom\_components/ha\_carrier/sensor.py                             |      318 |       28 |       58 |       16 |     88% |90-\>97, 104-\>110, 110-\>116, 137-\>154, 194, 211, 314-316, 324-330, 339-346, 378-384, 537, 595-596, 625-626, 655-656, 720-721, 788, 864-865 |
| custom\_components/ha\_carrier/util.py                               |       70 |        5 |       34 |        5 |     90% |96, 100, 133, 136, 144 |
| **TOTAL**                                                            | **1935** |  **186** |  **570** |  **138** | **86%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fdahlb%2Fha_carrier%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.