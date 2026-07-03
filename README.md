# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                 |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| custom\_components/ha\_carrier/\_\_init\_\_.py                       |      119 |       12 |       18 |        6 |     87% |129, 131-133, 164-165, 198, 239, 242-245, 249, 256, 272-\>277 |
| custom\_components/ha\_carrier/binary\_sensor.py                     |       54 |        2 |        6 |        2 |     93% |37-\>43, 77, 93 |
| custom\_components/ha\_carrier/carrier\_data\_update\_coordinator.py |      171 |       20 |       46 |        8 |     85% |157, 227, 232-233, 274, 311-317, 336-\>342, 346-353, 387-388, 394, 429-432, 444-\>443, 446, 464, 479-480 |
| custom\_components/ha\_carrier/carrier\_entity.py                    |       80 |        9 |       24 |        9 |     83% |70-77, 112, 127-\>126, 129-130, 144-\>143, 146-147, 187, 213-\>215 |
| custom\_components/ha\_carrier/climate.py                            |      228 |       25 |       84 |       18 |     84% |68-\>70, 70-\>72, 72-\>75, 138, 162, 187, 197, 202, 219, 293-\>exit, 304-305, 335-344, 369, 409-\>412, 456, 461-462, 464-465, 468 |
| custom\_components/ha\_carrier/config\_flow.py                       |      125 |        5 |       38 |        9 |     91% |58-\>65, 66-67, 152-\>169, 211, 219, 263-\>292, 278-\>283, 284 |
| custom\_components/ha\_carrier/const.py                              |       31 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/diagnostics.py                        |       32 |        0 |        8 |        2 |     95% |63-\>46, 80-\>87 |
| custom\_components/ha\_carrier/entry\_level\_climate.py              |      126 |       17 |       36 |       17 |     79% |115, 146, 154, 157, 159, 161, 169, 174, 182, 190, 200, 203-\>208, 205, 209, 211, 237, 248-\>250, 254, 258 |
| custom\_components/ha\_carrier/exceptions.py                         |        2 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/migrate.py                            |      234 |       41 |      112 |       26 |     78% |96, 112, 127, 177, 181-183, 187, 221, 223-231, 242-247, 251, 270-279, 284-293, 298, 344-\>351, 415-\>418, 418-\>420, 420-\>422, 422-\>425, 437-\>452, 463-\>452, 496, 505-514, 519-528, 593-\>601, 596-597, 602, 670-\>677, 673-674 |
| custom\_components/ha\_carrier/resiliency.py                         |      104 |        7 |       30 |        5 |     91% |167-173, 260, 266, 273, 279, 283 |
| custom\_components/ha\_carrier/select.py                             |       51 |        6 |        8 |        4 |     83% |65, 81, 110, 126, 157-158 |
| custom\_components/ha\_carrier/sensor.py                             |      285 |       28 |       56 |       16 |     87% |90-\>96, 96-\>102, 102-\>108, 129-\>146, 186, 203, 303-305, 313-319, 328-335, 366-372, 518, 574-575, 603-604, 632-633, 695-696, 758, 823-824 |
| custom\_components/ha\_carrier/util.py                               |       66 |        5 |       34 |        5 |     90% |83, 87, 120, 123, 131 |
| **TOTAL**                                                            | **1708** |  **177** |  **500** |  **127** | **85%** |           |


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